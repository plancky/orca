"""Google OAuth 2.0 web flow (Phase 2 — replaces the Phase 1 501 stub).

``GET /auth/google`` starts the offline-access consent flow (CSRF ``state`` in
Redis); ``GET /auth/callback`` exchanges the code, resolves the Google
account email, upserts the user, stores Fernet-encrypted tokens, enqueues an
initial sync, and mints a JWT so the connected account can query immediately.

``google*`` imports are lazy so importing this router never pulls the Google
stack into the mock/offline path.
"""

import asyncio
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from backend.api.deps import SessionDep, get_redis
from backend.config import settings
from backend.core.security import create_access_token
from backend.db.models import User
from backend.providers.google import credentials as creds_mod

# oauthlib reads these from os.environ, not pydantic settings. RELAX_TOKEN_SCOPE
# is unconditional: Google expands/canonicalizes scopes (email -> userinfo.email)
# so its returned set never equals the request, in dev AND prod. INSECURE_TRANSPORT
# is gated to http://localhost so a real https redirect keeps transport enforced.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
if settings.GOOGLE_REDIRECT_URI.startswith(("http://localhost", "http://127.0.0.1")):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

router = APIRouter(prefix="/auth", tags=["auth"])

_STATE_TTL_SECONDS = 600
# Google-connected accounts authenticate via OAuth, not a local password.
_OAUTH_PLACEHOLDER_HASH = "!google-oauth-no-password"


def _require_configured() -> None:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    # The callback Fernet-encrypts tokens; fail fast with a clear 503 here rather
    # than a late 500 after a successful Google code exchange.
    if not settings.TOKEN_ENCRYPTION_KEY:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured (TOKEN_ENCRYPTION_KEY unset)",
        )


def _spa_redirect(**fragment: str) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.FRONTEND_URL}/auth/callback#{urlencode(fragment)}",
        status_code=302,
    )


def _build_flow():
    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": settings.GOOGLE_AUTH_URI,
            "token_uri": settings.GOOGLE_TOKEN_URI,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=settings.GOOGLE_SCOPES.split(),
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


async def _fetch_userinfo_email(creds) -> str:
    from googleapiclient.discovery import build

    def _call() -> dict:
        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return service.userinfo().get().execute()

    info = await asyncio.to_thread(_call)
    email = info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")
    return email


@router.get("/google")
async def google_authorize() -> RedirectResponse:
    _require_configured()
    state = secrets.token_urlsafe(32)
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    # authorization_url() generated a PKCE code_verifier and sent its S256
    # challenge to Google; the callback runs a fresh Flow and must replay THIS
    # verifier at token exchange, so persist it under the single-use, TTL'd state.
    redis = get_redis()
    await redis.set(
        f"oauth:state:{state}", flow.code_verifier or "", ex=_STATE_TTL_SECONDS
    )
    return RedirectResponse(auth_url)


@router.get("/callback")
async def google_callback(
    session: SessionDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    _require_configured()
    redis = get_redis()
    # Atomic single-use consume: GETDEL returns the stored PKCE verifier ("" if
    # none), or None when the state is unknown / expired / already replayed.
    code_verifier = await redis.getdel(f"oauth:state:{state}")
    if code_verifier is None:
        return _spa_redirect(error="invalid_state")
    # Consent denied (or Google returned an error): ``?error=access_denied&state=``
    # arrives with a valid state but no ``code``. Hand the reason back to the SPA
    # instead of 422-ing on the missing ``code``.
    if error:
        return _spa_redirect(error=error)
    if not code:
        return _spa_redirect(error="missing_code")

    flow = _build_flow()
    if code_verifier:
        flow.code_verifier = code_verifier
    await asyncio.to_thread(flow.fetch_token, code=code)
    creds = flow.credentials
    email = await _fetch_userinfo_email(creds)

    from backend import crud

    user = await crud.get_user_by_email(session, email)
    if user is None:
        user = User(
            email=email,
            full_name=email,
            hashed_password=_OAUTH_PLACEHOLDER_HASH,
            is_active=True,
            timezone=settings.DEFAULT_TZ,
        )
        session.add(user)
        await session.flush()

    await creds_mod.store_credentials(session, user, creds)

    from backend.workers.sync import sync_all_users

    sync_all_users.delay()

    access_token = create_access_token(
        user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return _spa_redirect(token=access_token, email=email)

"""OAuth wiring QA — ASGI round-trips for the Google OAuth login flow.

Mirrors ``tests/test_auth_qa.py``: ``httpx.ASGITransport`` against the real app,
``asyncio.run`` per test, ``get_session`` overridden to the NullPool factory, and
redirects NOT followed (the 302/307 ``Location`` is the assertion surface).

Pins the redirect_uri reconciliation to the registered web-client callback
``http://localhost:5173/api/v1/auth/callback``:

  A  GET /auth/google          -> redirect to accounts.google.com threading the
                                  configured client_id + redirect_uri (offline).
  B  GET /auth/callback?state= -> 302 to the SPA with error=invalid_state
                                  (proves the callback is served at /callback,
                                  renamed from /google/callback).
  C  Settings default GOOGLE_REDIRECT_URI is the 5173 callback.
  D  authorize persists the PKCE code_verifier under the state key.
  E  the callback restores that verifier onto the fresh Flow before exchange.

Run + capture evidence:
    REDIS_URL=redis://localhost:6399/0 \\
      uv run pytest tests/test_auth_oauth_qa.py -v | tee .omo/artifacts/oauth-qa.log
"""

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport

from backend.api.deps import get_redis
from backend.config import Settings, settings
from backend.db.session import async_session_factory, get_session
from backend.main import app

API = settings.API_V1_STR
REGISTERED_REDIRECT = "http://localhost:5173/api/v1/auth/callback"


async def _override_get_session():
    async with async_session_factory() as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _configure(monkeypatch) -> None:
    """Set OAuth client creds + the registered redirect on the live settings."""
    monkeypatch.setattr(
        settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com"
    )
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", REGISTERED_REDIRECT)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")


# --------------------------------------------------------------------------- #
# A: authorize -> redirect to Google, threading client_id + redirect_uri.
# --------------------------------------------------------------------------- #
async def _authorize() -> None:
    async with _client() as client:
        r = await client.get(f"{API}/auth/google")
    assert r.status_code in (302, 307), r.text
    loc = r.headers["location"]
    parsed = urlparse(loc)
    assert parsed.netloc == "accounts.google.com", loc
    q = parse_qs(parsed.query)
    assert q["client_id"][0] == "test-client-id.apps.googleusercontent.com", loc
    assert q["redirect_uri"][0] == REGISTERED_REDIRECT, loc
    assert q["access_type"][0] == "offline", loc


def test_authorize_redirects_to_google_with_registered_uri(monkeypatch) -> None:
    _configure(monkeypatch)
    asyncio.run(_authorize())


# --------------------------------------------------------------------------- #
# B: callback served at /auth/callback (renamed); unknown state -> invalid_state
#    redirect back to the SPA. Pre-rename this path is 404.
# --------------------------------------------------------------------------- #
async def _callback_invalid_state() -> None:
    async with _client() as client:
        r = await client.get(
            f"{API}/auth/callback", params={"state": "nope", "code": "x"}
        )
    assert r.status_code == 302, f"{r.status_code} {r.text}"
    loc = r.headers["location"]
    assert loc.startswith(f"{settings.FRONTEND_URL}/auth/callback#"), loc
    assert "error=invalid_state" in loc, loc


def test_callback_served_at_renamed_path_invalid_state(monkeypatch) -> None:
    _configure(monkeypatch)
    asyncio.run(_callback_invalid_state())


# --------------------------------------------------------------------------- #
# C: the shipped default matches the registered web-client redirect URI.
# --------------------------------------------------------------------------- #
def test_default_redirect_uri_matches_registered() -> None:
    assert Settings(_env_file=None).GOOGLE_REDIRECT_URI == REGISTERED_REDIRECT


# --------------------------------------------------------------------------- #
# D: authorize persists the PKCE code_verifier under the state key. The callback
#    runs a fresh Flow and must replay THIS verifier or Google rejects the
#    exchange (invalid_grant). Pre-fix the stored value is a placeholder "1".
# --------------------------------------------------------------------------- #
async def _authorize_persists_verifier() -> None:
    async with _client() as client:
        r = await client.get(f"{API}/auth/google")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    stored = await get_redis().getdel(f"oauth:state:{state}")
    assert stored is not None, "authorize must persist a value under the state key"
    assert len(stored) >= 43, f"expected a PKCE verifier (>=43 chars), got {stored!r}"


def test_authorize_persists_pkce_verifier(monkeypatch) -> None:
    _configure(monkeypatch)
    asyncio.run(_authorize_persists_verifier())


# --------------------------------------------------------------------------- #
# E: the callback restores the stored verifier onto the fresh Flow BEFORE the
#    token exchange. A fake Flow captures the verifier then aborts the exchange.
# --------------------------------------------------------------------------- #
_CAPTURED: dict = {}


class _StopExchange(Exception):
    pass


class _FakeFlow:
    def __init__(self) -> None:
        self.code_verifier = None

    def fetch_token(self, code=None):
        _CAPTURED["code_verifier"] = self.code_verifier
        _CAPTURED["code"] = code
        raise _StopExchange


async def _callback_restores_verifier() -> None:
    state = "known-state-e"
    verifier = "v" * 120
    await get_redis().set(f"oauth:state:{state}", verifier, ex=600)
    async with _client() as client:
        with pytest.raises(_StopExchange):
            await client.get(
                f"{API}/auth/callback", params={"state": state, "code": "the-code"}
            )
    assert _CAPTURED["code_verifier"] == verifier
    assert _CAPTURED["code"] == "the-code"


def test_callback_restores_pkce_verifier_before_exchange(monkeypatch) -> None:
    _configure(monkeypatch)
    _CAPTURED.clear()
    monkeypatch.setattr("backend.api.routes_auth._build_flow", lambda: _FakeFlow())
    asyncio.run(_callback_restores_verifier())

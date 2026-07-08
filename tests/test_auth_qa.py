"""Wave B1 auth QA — ASGI round-trips (no curl, no live server).

Driven through ``httpx.ASGITransport`` against the real app. Each test wraps its
scenario in ``asyncio.run`` (matching the repo's smoke-test style); ``get_session``
is overridden to the NullPool factory so every fresh per-test event loop opens
its own asyncpg connection instead of reusing a pooled one from a prior loop.

Run + capture evidence:
    uv run pytest tests/test_auth_qa.py -v | tee .omo/artifacts/waveB1-qa.log
"""

import asyncio
import uuid

import httpx
from httpx import ASGITransport
from sqlmodel import select

from backend.config import settings
from backend.db.models import User
from backend.db.session import async_session_factory, get_session
from backend.main import app

API = settings.API_V1_STR
PASSWORD = "S3cur3-pass!word"


async def _override_get_session():
    async with async_session_factory() as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


def _unique_email() -> str:
    return f"qa-{uuid.uuid4().hex[:12]}@example.com"


async def _signup(client: httpx.AsyncClient, email: str, **extra) -> httpx.Response:
    body = {"email": email, "password": PASSWORD, "full_name": "QA User", **extra}
    return await client.post(f"{API}/users/signup", json=body)


async def _login(client: httpx.AsyncClient, email: str) -> httpx.Response:
    return await client.post(
        f"{API}/login/access-token",
        data={"username": email, "password": PASSWORD},
    )


async def _set_inactive(email: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.is_active = False
        session.add(user)
        await session.commit()


# --------------------------------------------------------------------------- #
# HAPPY: signup 201 -> login 200 (token) -> me 200.
# --------------------------------------------------------------------------- #
async def _happy() -> None:
    email = _unique_email()
    async with _client() as client:
        r = await _signup(client, email)
        assert r.status_code == 201, r.text
        assert r.json()["email"] == email
        assert r.json()["is_superuser"] is False

        r = await _login(client, email)
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        assert token
        assert r.json()["token_type"] == "bearer"

        r = await client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["email"] == email


def test_happy_signup_login_me() -> None:
    asyncio.run(_happy())


# --------------------------------------------------------------------------- #
# FAILURE: tampered token -> 401 + WWW-Authenticate: Bearer.
# --------------------------------------------------------------------------- #
async def _tampered_token() -> None:
    email = _unique_email()
    async with _client() as client:
        await _signup(client, email)
        token = (await _login(client, email)).json()["access_token"]
        tampered = token.rsplit(".", 1)[0] + ".invalidsignature"
        r = await client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {tampered}"}
        )
        assert r.status_code == 401, r.text
        assert r.headers.get("www-authenticate") == "Bearer"


def test_failure_tampered_token_401() -> None:
    asyncio.run(_tampered_token())


# --------------------------------------------------------------------------- #
# FAILURE: inactive user login -> 400.
# --------------------------------------------------------------------------- #
async def _inactive_user() -> None:
    email = _unique_email()
    async with _client() as client:
        await _signup(client, email)
        await _set_inactive(email)
        r = await _login(client, email)
        assert r.status_code == 400, r.text
        assert "inactive" in r.json()["detail"].lower()


def test_failure_inactive_user_400() -> None:
    asyncio.run(_inactive_user())


# --------------------------------------------------------------------------- #
# FAILURE: signup with is_superuser=true is ignored (priv-esc guard).
# --------------------------------------------------------------------------- #
async def _privilege_escalation_ignored() -> None:
    email = _unique_email()
    async with _client() as client:
        r = await _signup(client, email, is_superuser=True, is_active=True)
        assert r.status_code == 201, r.text
        assert r.json()["is_superuser"] is False


def test_failure_signup_privilege_escalation_ignored() -> None:
    asyncio.run(_privilege_escalation_ignored())


# --------------------------------------------------------------------------- #
# FAILURE: non-superuser POST /users/ -> 403.
# --------------------------------------------------------------------------- #
async def _non_superuser_forbidden() -> None:
    email = _unique_email()
    async with _client() as client:
        await _signup(client, email)
        token = (await _login(client, email)).json()["access_token"]
        r = await client.post(
            f"{API}/users/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "email": _unique_email(),
                "password": PASSWORD,
                "full_name": "New",
            },
        )
        assert r.status_code == 403, r.text


def test_failure_non_superuser_create_user_403() -> None:
    asyncio.run(_non_superuser_forbidden())

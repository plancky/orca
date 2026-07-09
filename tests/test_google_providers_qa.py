"""Hermetic tests for the Phase-2 Google providers.

No live Google: normalizers run on recorded JSON, write routing uses a
``MagicMock`` client, and credential refresh uses a fake creds object. The
mock-provider contract is covered by the existing suite and stays untouched.
"""

import base64
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db.models import ActionsLog, User
from backend.db.session import async_session_factory
from backend.providers.google import credentials as creds_mod
from backend.providers.google import drive, gcal, gmail
from backend.providers.google.provider import GoogleProvider, normalize_service

_GMAIL_COLS = {
    "email_id", "thread_id", "sender_email_id", "receiver_email_id",
    "subject", "content", "labels", "sent_at", "received_at",
}
_GCAL_COLS = {
    "event_id", "title", "description", "location",
    "start_at", "end_at", "attendees",
}
_GDRIVE_COLS = {"file_id", "name", "mime_type", "content", "owner", "modified_at"}


def _gmail_message() -> dict:
    body = base64.urlsafe_b64encode(b"Budget Q3 numbers attached.").decode()
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "IMPORTANT"],
        "internalDate": "1700000000000",
        "snippet": "snip",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "boss@x.com"},
                {"name": "To", "value": "me@x.com"},
                {"name": "Subject", "value": "Q3 Budget"},
                {"name": "Date", "value": "Tue, 14 Nov 2023 22:13:20 +0000"},
            ],
            "body": {"data": body},
        },
    }


class _FakeCreds:
    def __init__(self, raise_invalid: bool = False) -> None:
        self.token = "old"
        self.refresh_token = "refresh-1"
        self.expiry = datetime(2000, 1, 1)
        self.scopes = ["s1"]
        self._expired = True
        self._raise = raise_invalid

    @property
    def expired(self) -> bool:
        return self._expired

    def refresh(self, request) -> None:
        if self._raise:
            from google.auth.exceptions import RefreshError

            raise RefreshError("invalid_grant: Token has been expired or revoked.")
        self.token = "new-rotated"
        self._expired = False
        self.expiry = datetime(2099, 1, 1)


@pytest.fixture
def fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key)
    return key


def test_gmail_normalize_exact_columns():
    item = gmail._normalize(_gmail_message())
    assert set(item) == _GMAIL_COLS
    assert item["sender_email_id"] == "boss@x.com"
    assert item["subject"] == "Q3 Budget"
    assert "Budget Q3" in item["content"]
    assert item["labels"] == ["INBOX", "IMPORTANT"]
    assert item["received_at"].tzinfo is not None


def test_gcal_normalize_exact_columns_and_attendee_filter():
    event = {
        "id": "e1",
        "summary": "Acme sync",
        "description": "d",
        "location": "Room 4",
        "start": {"dateTime": "2024-01-02T10:00:00Z"},
        "end": {"dateTime": "2024-01-02T11:00:00Z"},
        "attendees": [{"email": "a@x.com"}, {"optional": True}],
    }
    item = gcal._normalize(event)
    assert set(item) == _GCAL_COLS
    assert item["title"] == "Acme sync"
    assert item["attendees"] == ["a@x.com"]
    assert item["start_at"].tzinfo is not None


def test_gcal_collect_splits_cancelled_into_removals():
    resp = {
        "items": [
            {"id": "keep", "summary": "x", "start": {}, "end": {}},
            {"id": "gone", "status": "cancelled"},
        ]
    }
    upserts: list[dict] = []
    removals: list[str] = []
    gcal._collect(resp, upserts, removals)
    assert [u["event_id"] for u in upserts] == ["keep"]
    assert removals == ["gone"]


def test_drive_normalize_binary_is_name_only():
    file = {
        "id": "f1",
        "name": "report.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "2024-03-01T09:00:00.000Z",
        "owners": [{"emailAddress": "own@x.com"}],
    }
    item = drive._normalize(None, file)
    assert set(item) == _GDRIVE_COLS
    assert item["content"] == "report.pdf"
    assert item["owner"] == "own@x.com"
    assert item["modified_at"].tzinfo is not None


def test_normalize_service_aliases():
    assert normalize_service("drive") == "gdrive"
    assert normalize_service("calendar") == "gcal"
    assert normalize_service("gmail") == "gmail"
    assert normalize_service("gcal") == "gcal"


def test_gmail_write_routing():
    client = MagicMock()
    gmail.write(client, "draft_email", {"to": "a@x.com", "subject": "s", "body": "b"})
    client.users().drafts().create.assert_called()
    gmail.write(client, "send_email", {"to": "a@x.com", "body": "b"})
    client.users().messages().send.assert_called()
    gmail.write(client, "update_labels", {"email_id": "m1", "add_labels": ["L"]})
    client.users().messages().modify.assert_called()
    with pytest.raises(ValueError):
        gmail.write(client, "bogus", {})


def test_gcal_write_routing():
    client = MagicMock()
    gcal.write(client, "create_event", {"title": "t", "start": "x", "end": "y"})
    client.events().insert.assert_called()
    gcal.write(client, "update_event", {"event_id": "e1", "title": "t2"})
    client.events().patch.assert_called()
    gcal.write(client, "delete_event", {"event_id": "e1"})
    client.events().delete.assert_called()


def test_drive_write_routing():
    client = MagicMock()
    drive.write(client, "share_file", {"file_id": "f1", "email": "a@x.com"})
    client.permissions().create.assert_called()
    drive.write(client, "create_folder", {"name": "F"})
    client.files().create.assert_called()
    drive.write(client, "move_file", {"file_id": "f1", "add_parents": "p2"})
    client.files().update.assert_called()


def test_fernet_round_trip(fernet_key):
    assert creds_mod.decrypt_token(creds_mod.encrypt_token("secret")) == "secret"


async def test_google_search_uses_canonical_service(monkeypatch):
    captured: dict = {}

    async def _fake_hybrid(session, q, service, user_id, filters=None, top_k=10):
        captured["service"] = service
        return []

    monkeypatch.setattr(
        "backend.providers.google.provider.hybrid_search", _fake_hybrid
    )
    provider = GoogleProvider(session=object(), user_id=uuid.uuid4())
    result = await provider.search("drive", "quarterly report", {})
    assert captured["service"] == "gdrive"
    assert result == []


async def test_google_search_blank_query_skips_embedding(monkeypatch):
    # Given: a date-only ask ("meetings last week") arrives with no query text.
    captured: dict = {}

    async def _fake_filter(session, service, user_id, filters=None, top_k=10):
        captured["service"] = service
        captured["filters"] = filters
        return [{"event_id": "e1"}]

    async def _boom_embed(text, user_id=None):
        raise AssertionError("blank query must not be embedded")

    monkeypatch.setattr(
        "backend.providers.google.provider.filter_search", _fake_filter
    )
    monkeypatch.setattr(
        "backend.providers.google.provider.embedder.embed_query", _boom_embed
    )
    window = {"start_at": {"start": "2026-07-01T00:00:00+00:00"}}

    # When: search runs with an empty query but a start_at filter.
    provider = GoogleProvider(session=object(), user_id=uuid.uuid4())
    result = await provider.search("calendar", "", {"start_at": window["start_at"]})

    # Then: it routes to the filter-only path (no embedding) with the canonical
    # service and the metadata preserved.
    assert captured["service"] == "gcal"
    assert captured["filters"] == {"start_at": window["start_at"]}
    assert result == [{"event_id": "e1"}]


async def test_execute_dry_run_is_simulated(fernet_key, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER", "google")
    monkeypatch.setattr(settings, "DRY_RUN_WRITES", True)
    uid = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(User(id=uid, email=f"dry_{uid}@x.com", hashed_password="x"))
        await session.commit()
        provider = GoogleProvider(session=session, user_id=uid)
        res = await provider.execute("gmail", "send_email", {"to": "a@x.com"})
        assert res["status"] == "simulated"
        rows = (
            await session.execute(
                select(ActionsLog).where(ActionsLog.user_id == uid)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].tool == "gmail.send_email"
        assert rows[0].status == "simulated"


async def test_store_credentials_encrypts_at_rest(fernet_key):
    uid = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(id=uid, email=f"store_{uid}@x.com", hashed_password="x")
        session.add(user)
        await session.commit()
        creds = _FakeCreds()
        creds.token = "tok"
        creds.refresh_token = "rt"
        creds.expiry = datetime(2099, 1, 1)
        creds.scopes = ["a", "b"]
        await creds_mod.store_credentials(session, user, creds)
        await session.refresh(user)
        assert creds_mod.decrypt_token(user.google_access_token) == "tok"
        assert creds_mod.decrypt_token(user.google_refresh_token) == "rt"
        assert user.token_scopes == ["a", "b"]
        assert user.auth_status == "valid"
        assert user.token_expiry is not None


async def test_credentials_refresh_rotates_and_persists(fernet_key, monkeypatch):
    uid = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(
            id=uid,
            email=f"refresh_{uid}@x.com",
            hashed_password="x",
            google_access_token=creds_mod.encrypt_token("old"),
            google_refresh_token=creds_mod.encrypt_token("refresh-1"),
            token_expiry=datetime(2000, 1, 1, tzinfo=timezone.utc),
            token_scopes=["s1"],
            auth_status="valid",
        )
        session.add(user)
        await session.commit()

        monkeypatch.setattr(creds_mod, "build_credentials", lambda u: _FakeCreds())
        creds = await creds_mod.credentials_for(session, uid)
        assert creds.token == "new-rotated"
        await session.refresh(user)
        assert creds_mod.decrypt_token(user.google_access_token) == "new-rotated"
        assert user.auth_status == "valid"


async def test_credentials_invalid_grant_marks_invalid(fernet_key, monkeypatch):
    from google.auth.exceptions import RefreshError

    uid = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(
            id=uid,
            email=f"invalid_{uid}@x.com",
            hashed_password="x",
            google_access_token=creds_mod.encrypt_token("old"),
            google_refresh_token=creds_mod.encrypt_token("refresh-1"),
            token_expiry=datetime(2000, 1, 1, tzinfo=timezone.utc),
            auth_status="valid",
        )
        session.add(user)
        await session.commit()

        monkeypatch.setattr(
            creds_mod, "build_credentials", lambda u: _FakeCreds(raise_invalid=True)
        )
        with pytest.raises(RefreshError):
            await creds_mod.credentials_for(session, uid)
        await session.refresh(user)
        assert user.auth_status == "invalid"

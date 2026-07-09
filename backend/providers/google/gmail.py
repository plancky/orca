"""Gmail sync + get + write adapter (Phase 2).

Normalizes Gmail API JSON into ``gmail_datasource`` column dicts (chunking +
embedding happen in ``workers/sync.py``, not here). ``sync`` returns
``(upserts, removals, next_cursor)`` where the cursor is a Gmail ``historyId``;
``get_full`` returns one message's full decoded body; ``write`` dispatches the
draft/send/label agent verbs.

``googleapiclient`` is imported lazily (only ``HttpError``, inside the functions
that catch it) so the mock/offline path never loads the Google stack.
"""

import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from backend.config import settings

SERVICE_NAME = "gmail"
SERVICE_VERSION = "v1"
KEY_FIELD = "email_id"

_USER = "me"


def sync(client, cursor: str | None) -> tuple[list[dict], list[str], str | None]:
    if cursor:
        from googleapiclient.errors import HttpError

        try:
            return _incremental(client, cursor)
        except HttpError as exc:
            if exc.resp.status == 404:
                return _full(client)
            raise
    return _full(client)


def get_full(client, item_id: str) -> dict:
    msg = (
        client.users()
        .messages()
        .get(userId=_USER, id=item_id, format="full")
        .execute()
    )
    return _normalize(msg)


def write(client, action: str, args: dict) -> dict:
    if action == "draft_email":
        raw = _build_raw(args)
        return (
            client.users()
            .drafts()
            .create(userId=_USER, body={"message": {"raw": raw}})
            .execute()
        )
    if action == "send_email":
        raw = _build_raw(args)
        return (
            client.users().messages().send(userId=_USER, body={"raw": raw}).execute()
        )
    if action == "update_labels":
        body: dict = {}
        if args.get("add_labels"):
            body["addLabelIds"] = args["add_labels"]
        if args.get("remove_labels"):
            body["removeLabelIds"] = args["remove_labels"]
        mid = args.get("email_id") or args.get("id")
        return (
            client.users().messages().modify(userId=_USER, id=mid, body=body).execute()
        )
    raise ValueError(f"unknown gmail action: {action}")


def _full(client) -> tuple[list[dict], list[str], str | None]:
    upserts: list[dict] = []
    page_token = None
    cap = settings.SYNC_PAGE_SIZE * 5
    query = f"newer_than:{settings.SYNC_LOOKBACK_DAYS}d"
    while True:
        resp = (
            client.users()
            .messages()
            .list(
                userId=_USER,
                q=query,
                maxResults=settings.SYNC_PAGE_SIZE,
                pageToken=page_token,
            )
            .execute()
        )
        for ref in resp.get("messages", []):
            msg = (
                client.users()
                .messages()
                .get(userId=_USER, id=ref["id"], format="full")
                .execute()
            )
            upserts.append(_normalize(msg))
        page_token = resp.get("nextPageToken")
        if not page_token or len(upserts) >= cap:
            break
    return upserts, [], _current_history_id(client)


def _incremental(client, cursor: str) -> tuple[list[dict], list[str], str | None]:
    from googleapiclient.errors import HttpError

    added: set[str] = set()
    deleted: set[str] = set()
    page_token = None
    latest = cursor
    while True:
        resp = (
            client.users()
            .history()
            .list(userId=_USER, startHistoryId=cursor, pageToken=page_token)
            .execute()
        )
        for hist in resp.get("history", []):
            for item in hist.get("messagesAdded", []):
                added.add(item["message"]["id"])
            for item in hist.get("messagesDeleted", []):
                deleted.add(item["message"]["id"])
        if resp.get("historyId"):
            latest = str(resp["historyId"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    removals = list(deleted)
    upserts: list[dict] = []
    for mid in added - deleted:
        try:
            msg = (
                client.users()
                .messages()
                .get(userId=_USER, id=mid, format="full")
                .execute()
            )
            upserts.append(_normalize(msg))
        except HttpError as exc:
            if exc.resp.status == 404:
                removals.append(mid)
            else:
                raise
    return upserts, removals, latest


def _current_history_id(client) -> str | None:
    profile = client.users().getProfile(userId=_USER).execute()
    hid = profile.get("historyId")
    return str(hid) if hid else None


def _normalize(msg: dict) -> dict:
    payload = msg.get("payload") or {}
    return {
        "email_id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender_email_id": _header(payload, "From"),
        "receiver_email_id": _header(payload, "To"),
        "subject": _header(payload, "Subject"),
        "content": _decode_body(payload) or msg.get("snippet") or "",
        "labels": list(msg.get("labelIds") or []),
        "sent_at": _parse_rfc2822(_header(payload, "Date")),
        "received_at": _ms_to_dt(msg.get("internalDate")),
    }


def _header(payload: dict, name: str) -> str | None:
    for head in payload.get("headers") or []:
        if head.get("name", "").lower() == name.lower():
            return head.get("value")
    return None


def _decode_body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    return re.sub(r"<[^>]+>", " ", html).strip() if html else ""


def _find_part(payload: dict, mime: str) -> str:
    if payload.get("mimeType") == mime:
        data = (payload.get("body") or {}).get("data")
        if data:
            return _b64url(data)
    for part in payload.get("parts") or []:
        found = _find_part(part, mime)
        if found:
            return found
    return ""


def _b64url(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")


def _ms_to_dt(ms: str | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def _parse_rfc2822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _build_raw(args: dict) -> str:
    msg = EmailMessage()
    if args.get("to"):
        msg["To"] = args["to"]
    if args.get("subject"):
        msg["Subject"] = args["subject"]
    if args.get("from"):
        msg["From"] = args["from"]
    msg.set_content(args.get("body") or "")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()

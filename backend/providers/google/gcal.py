"""Google Calendar sync + get + write adapter (Phase 2).

Normalizes Calendar API JSON into ``gcal_datasource`` column dicts. ``sync``
returns ``(upserts, removals, next_cursor)`` where the cursor is a ``syncToken``;
cancelled events become removals. ``get_full`` returns one event; ``write``
dispatches the insert/patch/delete agent verbs.

``googleapiclient`` (only ``HttpError``) is imported lazily inside ``sync`` so
the mock/offline path never loads the Google stack.
"""

from datetime import datetime, timedelta, timezone

from backend.config import settings

SERVICE_NAME = "calendar"
SERVICE_VERSION = "v3"
KEY_FIELD = "event_id"

_CAL = "primary"


def sync(client, cursor: str | None) -> tuple[list[dict], list[str], str | None]:
    if cursor:
        from googleapiclient.errors import HttpError

        try:
            return _incremental(client, cursor)
        except HttpError as exc:
            if exc.resp.status == 410:
                return _full(client)
            raise
    return _full(client)


def get_full(client, item_id: str) -> dict:
    event = client.events().get(calendarId=_CAL, eventId=item_id).execute()
    return _normalize(event)


def write(client, action: str, args: dict) -> dict:
    if action == "create_event":
        return client.events().insert(calendarId=_CAL, body=_event_body(args)).execute()
    if action == "update_event":
        eid = args.get("event_id") or args.get("id")
        return (
            client.events()
            .patch(calendarId=_CAL, eventId=eid, body=_event_body(args))
            .execute()
        )
    if action == "delete_event":
        eid = args.get("event_id") or args.get("id")
        client.events().delete(calendarId=_CAL, eventId=eid).execute()
        return {"event_id": eid, "deleted": True}
    raise ValueError(f"unknown gcal action: {action}")


def _incremental(client, cursor: str) -> tuple[list[dict], list[str], str | None]:
    upserts: list[dict] = []
    removals: list[str] = []
    page_token = None
    next_sync = cursor
    while True:
        resp = (
            client.events()
            .list(calendarId=_CAL, syncToken=cursor, pageToken=page_token)
            .execute()
        )
        _collect(resp, upserts, removals)
        next_sync = resp.get("nextSyncToken") or next_sync
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return upserts, removals, next_sync


def _full(client) -> tuple[list[dict], list[str], str | None]:
    upserts: list[dict] = []
    removals: list[str] = []
    time_min = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    page_token = None
    next_sync = None
    while True:
        resp = (
            client.events()
            .list(
                calendarId=_CAL,
                singleEvents=True,
                timeMin=time_min,
                maxResults=settings.SYNC_PAGE_SIZE,
                pageToken=page_token,
            )
            .execute()
        )
        _collect(resp, upserts, removals)
        next_sync = resp.get("nextSyncToken") or next_sync
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return upserts, removals, next_sync


def _collect(resp: dict, upserts: list[dict], removals: list[str]) -> None:
    for event in resp.get("items", []):
        if event.get("status") == "cancelled":
            removals.append(event["id"])
        else:
            upserts.append(_normalize(event))


def _normalize(event: dict) -> dict:
    return {
        "event_id": event["id"],
        "title": event.get("summary"),
        "description": event.get("description"),
        "location": event.get("location"),
        "start_at": _parse_dt((event.get("start") or {})),
        "end_at": _parse_dt((event.get("end") or {})),
        "attendees": [
            a["email"] for a in event.get("attendees", []) if a.get("email")
        ],
    }


def _parse_dt(node: dict) -> datetime | None:
    value = node.get("dateTime") or node.get("date")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_body(args: dict) -> dict:
    body: dict = {}
    summary = args.get("title") or args.get("summary")
    if summary:
        body["summary"] = summary
    if args.get("description"):
        body["description"] = args["description"]
    if args.get("location"):
        body["location"] = args["location"]
    if args.get("start"):
        body["start"] = {"dateTime": args["start"]}
    if args.get("end"):
        body["end"] = {"dateTime": args["end"]}
    if args.get("attendees"):
        body["attendees"] = [{"email": e} for e in args["attendees"]]
    return body

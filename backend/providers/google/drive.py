"""Google Drive sync + get + write adapter (Phase 2).

Normalizes Drive API JSON into ``gdrive_datasource`` column dicts. ``sync``
returns ``(upserts, removals, next_cursor)`` where the cursor is a changes
``pageToken``; trashed/removed files become removals. Native Docs are exported
to text/plain, text files fetched, other binaries kept name-only (size-capped).

``googleapiclient`` is imported lazily so the mock/offline path never loads the
Google stack.
"""

from datetime import datetime, timedelta, timezone

from backend.config import settings

SERVICE_NAME = "drive"
SERVICE_VERSION = "v3"
KEY_FIELD = "file_id"

_FILE_FIELDS = "id, name, mimeType, modifiedTime, owners(emailAddress), trashed"
_MAX_CONTENT_CHARS = 100_000
_GOOGLE_DOC = "application/vnd.google-apps.document"


def sync(client, cursor: str | None) -> tuple[list[dict], list[str], str | None]:
    if cursor:
        return _incremental(client, cursor)
    return _full(client)


def get_full(client, item_id: str) -> dict:
    file = client.files().get(fileId=item_id, fields=_FILE_FIELDS).execute()
    return _normalize(client, file)


def write(client, action: str, args: dict) -> dict:
    if action == "share_file":
        fid = args.get("file_id") or args.get("id")
        permission = {
            "type": args.get("type", "user"),
            "role": args.get("role", "reader"),
        }
        if args.get("email"):
            permission["emailAddress"] = args["email"]
        return (
            client.permissions()
            .create(fileId=fid, body=permission, fields="id")
            .execute()
        )
    if action == "create_folder":
        body = {
            "name": args.get("name", "New Folder"),
            "mimeType": "application/vnd.google-apps.folder",
        }
        if args.get("parent"):
            body["parents"] = [args["parent"]]
        return client.files().create(body=body, fields="id, name").execute()
    if action == "move_file":
        fid = args.get("file_id") or args.get("id")
        kwargs: dict = {"fileId": fid, "fields": "id, parents"}
        add = args.get("add_parents") or args.get("parent")
        remove = args.get("remove_parents")
        if add:
            kwargs["addParents"] = add if isinstance(add, str) else ",".join(add)
        if remove:
            kwargs["removeParents"] = (
                remove if isinstance(remove, str) else ",".join(remove)
            )
        return client.files().update(**kwargs).execute()
    raise ValueError(f"unknown drive action: {action}")


def _full(client) -> tuple[list[dict], list[str], str | None]:
    upserts: list[dict] = []
    page_token = None
    cap = settings.SYNC_PAGE_SIZE * 5
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.SYNC_LOOKBACK_DAYS)
    query = f"trashed=false and modifiedTime > '{cutoff.isoformat()}'"
    while True:
        resp = (
            client.files()
            .list(
                q=query,
                orderBy="modifiedTime desc",
                pageSize=settings.SYNC_PAGE_SIZE,
                pageToken=page_token,
                fields=f"nextPageToken, files({_FILE_FIELDS})",
            )
            .execute()
        )
        for file in resp.get("files", []):
            upserts.append(_normalize(client, file))
        page_token = resp.get("nextPageToken")
        if not page_token or len(upserts) >= cap:
            break
    start = client.changes().getStartPageToken().execute()
    return upserts, [], start.get("startPageToken")


def _incremental(client, cursor: str) -> tuple[list[dict], list[str], str | None]:
    upserts: list[dict] = []
    removals: list[str] = []
    page_token = cursor
    new_start = cursor
    fields = (
        "nextPageToken, newStartPageToken, "
        f"changes(fileId, removed, file({_FILE_FIELDS}))"
    )
    while True:
        resp = (
            client.changes()
            .list(pageToken=page_token, includeRemoved=True, fields=fields)
            .execute()
        )
        for change in resp.get("changes", []):
            file = change.get("file")
            if change.get("removed") or (file and file.get("trashed")):
                removals.append(change.get("fileId"))
            elif file:
                upserts.append(_normalize(client, file))
        new_start = resp.get("newStartPageToken") or new_start
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return upserts, removals, new_start


def _normalize(client, file: dict) -> dict:
    owners = file.get("owners") or []
    return {
        "file_id": file["id"],
        "name": file.get("name"),
        "mime_type": file.get("mimeType"),
        "content": _excerpt(client, file),
        "owner": owners[0].get("emailAddress") if owners else None,
        "modified_at": _parse_dt(file.get("modifiedTime")),
    }


def _excerpt(client, file: dict) -> str:
    mime = file.get("mimeType", "")
    fid = file["id"]
    name = file.get("name") or ""
    try:
        if mime == _GOOGLE_DOC:
            raw = client.files().export(fileId=fid, mimeType="text/plain").execute()
        elif mime.startswith("text/"):
            raw = client.files().get_media(fileId=fid).execute()
        else:
            return name
    except Exception:
        return name
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return text[:_MAX_CONTENT_CHARS] or name


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

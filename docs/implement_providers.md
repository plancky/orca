# Phase 2: Real Google Workspace Providers

## Context

Phase 1 (`docs/PLAN.md`) ships a working orchestrator against a **mock data source**: a seeded corpus
behind a `Provider` interface, with `GoogleProvider` left as a stub and `/auth/google` stubbed. That
was deliberate — the rubric weights orchestration, embeddings, and scaling over Google API plumbing,
so Google is kept out of the critical path.

This phase makes the real plumbing work **behind the exact same `Provider` interface**, so switching
mock → real is a config flag (`PROVIDER=mock|google`), not a rewrite.

### Load-bearing architectural invariant

`search()` **stays cache-backed** — the hybrid pgvector search over `*_cache` is identical for Mock
and Google. Google APIs are touched only for:

- **(a) ingestion/sync** that fills the cache,
- **(b) `get()` / `get_context()`** — live full-content fetch for the extractor,
- **(c) `execute()` writes**.

This preserves the <500ms search target and keeps Google's flaky, rate-limited APIs off the read hot
path. Everything above the interface — planner, executor, agents, synthesizer, hybrid search — is
**untouched**. Only ingestion + `get` + `execute` get real implementations.

### Decisions

| Decision | Choice | Note |
|---|---|---|
| Google client lib | `google-api-python-client` (official) | Sync → `asyncio.to_thread` in FastAPI path; native in Celery. Drawbacks in §11. |
| Freshness | Incremental 15-min Celery **poll** now; **push** (watch + Pub/Sub) documented as future | Drawbacks in §11. |
| Auth | OAuth2 web flow, offline access, tokens **encrypted at rest** (Fernet) | Testing-mode single account for the take-home; verification notes for prod. |
| Write path | Same `pending_actions` confirm gate as Phase 1; confirmed branch now hits real Google | `DRY_RUN_WRITES` flag for safe demos. |
| Provider swap | `config.PROVIDER = mock \| google` via a provider factory | Everything above the interface unchanged. |

---

## 1. Interface recap (unchanged)

`Provider`: `search(service, query, filters)`, `get(service, id)`, `execute(service, action, args)`.
`GoogleProvider` implements the same three. `search()` delegates to the existing
`embeddings/search.py::hybrid_search` over `*_cache` — **no Google call**. Only `get`/`execute` and
the sync ingestion differ from `MockProvider`.

## 2. New dependencies

`google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `cryptography` (Fernet). Add to
`requirements.txt` / `pyproject.toml`. The CPU-only Docker image is unaffected (inference stays
external).

## 3. Config additions (`app/config.py`)

`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_SCOPES`,
`TOKEN_ENCRYPTION_KEY` (Fernet key), `PROVIDER` (`mock|google`), `DRY_RUN_WRITES` (bool),
`SYNC_PAGE_SIZE`, `GMAIL_BATCH_SIZE` (≤100), `GOOGLE_UNITS_PER_SEC` (250 quota budget).

## 4. OAuth 2.0 flow (`app/api/routes_auth.py` — replace stub)

Use `google_auth_oauthlib.flow.Flow.from_client_config`.

- **Scopes (least privilege)**: Gmail `gmail.modify` (read + labels + drafts + send) — tighter
  alternative: `gmail.readonly` + `gmail.send`; Calendar `calendar.events`; Drive `drive.readonly` +
  `drive.file`. Escalation to document: `share_file` / `move_file` on *arbitrary existing* files
  needs full `drive` scope (a restricted scope → triggers Google app verification).
- `GET /api/v1/auth/google`: build `authorization_url(access_type='offline',
  include_granted_scopes='true', prompt='consent', state=<CSRF>)`; store `state` in Redis; redirect.
  `access_type=offline` + `prompt=consent` guarantees a **refresh_token**.
- `GET /api/v1/auth/google/callback`: verify `state`, `flow.fetch_token(code=...)`, fetch userinfo
  email, upsert `users` row, store **encrypted** access + refresh token + expiry + granted scopes,
  enqueue an initial full sync (Celery).
- Note: OAuth **Testing mode** (unverified, ≤100 test users) is sufficient for the take-home; prod
  multi-tenant needs consent-screen verification, especially for `gmail.modify` / full `drive`.

## 5. Credential + token management (`app/providers/google/credentials.py`)

- Tokens stored **Fernet-encrypted** in `users.google_access_token` / `google_refresh_token`.
- `load_credentials(user)` → `google.oauth2.credentials.Credentials(token, refresh_token, token_uri,
  client_id, client_secret, scopes, expiry)`.
- Auto-refresh: on `creds.expired`, `creds.refresh(google.auth.transport.requests.Request())`, then
  persist the rotated token (re-encrypted). Guard with a **per-user Redis lock** to avoid concurrent
  refresh races (refresh-token rotation).
- Handle `invalid_grant` (revoked/expired refresh token) → mark user `auth_status=invalid`, stop
  their sync, surface "re-consent required".

## 6. GoogleProvider (`app/providers/google/provider.py` — implement stub)

- Lazy per-service client: `_service(name, ver)` = `build(name, ver, credentials=creds,
  cache_discovery=False)` (pin/cache the discovery doc to avoid per-call network cost).
- `search(...)` → delegates to `hybrid_search` (identical to Mock; proves the drop-in swap).
- `get(service, id)` → live full-content fetch (full email body, full event, exported file text) →
  normalized dict. Wrapped in `asyncio.to_thread` in the FastAPI path.
- `execute(service, action, args)` → dispatch to per-service writes; honors `DRY_RUN_WRITES` and the
  **unchanged** confirm gate: unconfirmed → `{status: pending_confirmation, preview}` (build a cheap
  preview, e.g. `drafts.create` for email); confirmed → real mutation, log `actions_log`
  executed/failed.

## 7. Per-service adapters (`app/providers/google/{gmail,gcal,drive}.py`)

Each provides: incremental + full fetch (sync), `get_full` (get_context), writes, and a
normalize → cache-row mapper reusing the existing `embedder` (batch + Redis cache).

### Gmail
- **Sync**: `users.messages.list(q=..., maxResults)` paginated → `BatchHttpRequest` of
  `users.messages.get(format=full/metadata)` (batch ≤100). **Incremental**:
  `users.history.list(startHistoryId=<cursor>)`.
- **Map → `gmail_cache`**: `email_id=id, thread_id, sender=From, subject, body_preview=snippet or
  decoded text (~2KB), labels=labelIds, received_at=internalDate`. Embed `subject+"\n"+body_preview`.
- **Writes**: `drafts.create` (safe), `messages.send` (gated), `messages.modify` labels (gated).
- **`get_full`**: `messages.get(format=full)` → decode MIME parts → full body for the extractor.

### Calendar
- **Sync**: `events.list(calendarId=primary, singleEvents=true, timeMin/timeMax, pageToken)`.
  **Incremental** via **`syncToken`**; on `410 GONE` (token expired) → full resync.
- **Map → `gcal_cache`**: `event_id, title=summary, description, location, start_at, end_at,
  attendees=[email]`. Embed `title+description+location`.
- **Writes**: `events.insert / patch / delete` (gated).
- **`get_full`**: `events.get`.

### Drive
- **Sync**: `files.list(q, fields, orderBy=modifiedTime desc, pageToken)`. **Incremental** via
  `changes.getStartPageToken` → `changes.list(pageToken)` (apply upserts + removals) → store
  `newStartPageToken`.
- **Content excerpt**: native Docs → `files.export(mimeType=text/plain)` (size-capped, truncate);
  binaries (PDF) → `files.get_media` + optional `pdfminer` extraction; skip oversized files
  (name-only).
- **Map → `gdrive_cache`**: `file_id, name, mime_type, content_excerpt, owner=owners[0],
  modified_at`.
- **Writes**: `permissions.create` share (gated), `files.create` folder (safe), `files.update`
  add/removeParents move (gated).

## 8. Sync worker (`app/workers/sync.py`)

Replace "refresh seed deltas" with provider-driven ingestion. Per active user + service: load creds
→ incremental fetch (fallback to full if no cursor / on `410`) → batch-embed new/changed rows →
upsert `*_cache` → delete removed → update `sync_status(last_synced_at, item_count, cursor)`.

Celery tasks are **sync**, so they call the client directly (**no `to_thread` needed** — a real
benefit of doing bulk work in Celery). Backoff/quota: retry `429` / `403 rateLimitExceeded` with
exponential backoff + jitter; per-user Redis **token bucket** against the 250 units/s budget; batch
to cut round-trips. Initial full sync enqueued at first auth.

## 9. Migration (`app/migrations/`)

One small Alembic revision: `users += token_expiry TIMESTAMP, token_scopes JSONB, auth_status`;
`sync_status += cursor TEXT` (holds `historyId` / `syncToken` / Drive pageToken). **No vector
changes.**

## 10. Provider selection / wiring

`config.PROVIDER` picks `MockProvider` vs `GoogleProvider` in a small `app/providers/factory.py` used
by `agents/base.py`. Planner, executor, agents, synthesizer, and hybrid search are unchanged.

## 11. Drawbacks (explicitly documented)

### `google-api-python-client` (chosen client lib)
- **Blocking**: every FastAPI-path call (`get_context`, write previews) must run in
  `asyncio.to_thread`, consuming the default threadpool; at high concurrency this bounds throughput
  and adds latency vs an async-native client. *Mitigation*: search is cache-backed (Google off the
  read hot path), all bulk work runs in Celery (sync-native), cap concurrent live calls.
- **Discovery-doc overhead**: `build()` fetches a discovery doc → pin/cache it (`cache_discovery`) to
  avoid per-call network cost and an external dependency in the hot path.
- Heavier dependency footprint; batching uses its own `BatchHttpRequest` object.
- *Escape hatch if async pressure bites*: move only the FastAPI-path calls to `aiogoogle` / thin
  httpx, keep the official client in Celery.

### Poll-baseline freshness (chosen sync strategy)
- **Staleness**: up to ~15-min lag — meets the assignment's <15min SLA but sits at its edge; an item
  arriving just after a tick is invisible until the next tick.
- **Idle quota cost at scale**: polling 1M users every 15 min is steady Google load even when nothing
  changed; incremental cursors keep payloads tiny, but each user still costs ≥1 list call / service /
  tick. *Mitigation*: jittered / staggered beat (avoid thundering herd), sync only active users,
  backoff.
- **Real fix = push (documented future work)**: Gmail `users.watch` → Pub/Sub topic → `POST
  /webhooks/google`; Calendar `events.watch` + Drive `changes.watch` channels (need periodic renewal
  + a public HTTPS endpoint). Cuts staleness to seconds and removes idle polling. Deferred because it
  needs Pub/Sub + a verified public webhook — out of scope for the take-home baseline.

## 12. Testing / verification

- **Unit**: mapping functions (Google JSON → cache row) against recorded fixtures; token refresh
  (expired → refreshed, `invalid_grant` → auth-invalid).
- **Integration (single throwaway test account, OAuth Testing mode)**: `/auth/google` → callback →
  initial sync → assert `*_cache` populated → run a real query end-to-end → verify search hits real
  items. Seed the account with a few emails / events / files matching the golden set.
- **Write path**: `draft_email` creates a real Gmail draft (safe); send / create / delete tested with
  `DRY_RUN_WRITES` then one real confirm on the test account; verify `actions_log`.
- **Incremental**: mutate the account (self-email / add event) → `POST /sync/trigger` → assert the
  delta appears and the cursor advanced.
- **Quota / backoff**: force or mock a `429` → assert retry + backoff, no crash.
- **Contract**: the existing golden-set eval + orchestrator tests must stay green with
  `PROVIDER=mock` (same interface).

## 13. Implementation order

1. Deps + config + Fernet key + `providers/factory.py` + `PROVIDER` flag.
2. Migration (users token fields, `sync_status.cursor`).
3. OAuth flow (`routes_auth`) + `credentials.py` (store / encrypt / load / refresh).
4. `GoogleProvider` skeleton delegating `search` to `hybrid_search` (prove the drop-in swap).
5. **Gmail** adapter (sync + map + get_full + writes) → wire into `sync.py` → first full sync →
   query works on real Gmail.
6. **Calendar**, then **Drive** adapter (same pattern).
7. Incremental cursors + backoff / quota + batch.
8. Write gate against real APIs (draft first, then gated mutations) + `DRY_RUN`.
9. Tests + docs: README (Google Cloud project / OAuth client setup, redirect URI, test users),
   DESIGN.md (push-future + drawbacks), API.md (callback route).

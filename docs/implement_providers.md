# Phase 2: Real Google Workspace Providers

## Context

Phase 1 (`docs/PLAN.md`) ships a working orchestrator against a **mock data source**: a seeded corpus
behind a `Provider` interface, with `GoogleProvider` left as a stub and `/auth/google` returning `501`.
That was deliberate — the rubric weights orchestration, embeddings, and scaling over Google API
plumbing, so Google is kept out of the critical path.

This phase makes the real plumbing work **behind the exact same `Provider` interface**, so switching
mock → real is a config flag (`PROVIDER=mock|google`), not a rewrite.

> **This document has been reconciled against the actual `backend/` tree** (not the original `app/…`
> sketch). All paths, table names, column names, and helper signatures below are the real ones. See
> §0 for what Phase 1 already built so Phase 2 doesn't re-do it.

### Load-bearing architectural invariant

`search()` **stays cache-backed** — the hybrid pgvector search over the `*_vector_store` chunk tables
(joined to `*_datasource`) is identical for Mock and Google. Google APIs are touched only for:

- **(a) ingestion/sync** that fills `*_datasource` + `*_vector_store`,
- **(b) `get()`** — live full-content fetch for the extractor (`get_context`),
- **(c) `execute()` writes**.

This preserves the <500ms search target and keeps Google's flaky, rate-limited APIs off the read hot
path. Everything above the interface — planner, executor, agents, synthesizer, hybrid search — is
**untouched**. Only ingestion + `get` + `execute` get real implementations.

### Decisions

| Decision | Choice | Note |
|---|---|---|
| Google client lib | `google-api-python-client` (official) | Sync → `asyncio.to_thread` on the **async executor path** (runs in Celery); native in the sync beat. Drawbacks in §11. |
| Freshness | Incremental 15-min Celery **poll** now (`SYNC_BEAT_MINUTES`, already wired); **push** (watch + Pub/Sub) documented as future | Drawbacks in §11. |
| Auth | OAuth2 web flow, offline access, tokens **encrypted at rest** (Fernet) | Testing-mode single account for the take-home; verification notes for prod. |
| Write path | Same `WRITE_TOOLS` executor suspend + `actions_log` confirm gate as Phase 1; confirmed branch now hits real Google | `DRY_RUN_WRITES` flag for safe demos. |
| Provider swap | `settings.PROVIDER = mock \| google` via the existing `backend/providers/factory.py` | Everything above the interface unchanged. |
| Embedding space | `PROVIDER=google` **requires `EMBED_MODE=real`** | Real Google items are embedded by the sync beat; query vectors must share that space (**Modal BGE**), else retrieval is meaningless. Under `EMBED_MODE=fake` the corpus is populated but not semantically searchable. |

---

## 0. Current backend state (grounded in `backend/`)

**Already built in Phase 1 — do NOT redo:**

- **Schema is a two-table split per service** (`backend/db/models.py`), NOT a single `*_cache`:
  - `gmail_datasource` (item) + `gmail_vector_store` (chunks) — plus `gcal_*` / `gdrive_*` mirrors.
  - `User` **already has** `google_access_token` + `google_refresh_token` (nullable `String`).
  - `sync_status` **already has** `cursor: str | None` (migration `0001_initial`).
- **`settings.PROVIDER`** (`mock`/`google`) and **`SYNC_BEAT_MINUTES`** already exist in
  [`backend/config.py`](../backend/config.py).
- **Provider factory** [`backend/providers/factory.py`](../backend/providers/factory.py) already
  branches on `settings.PROVIDER` and returns `MockProvider(session, user_id)` or `GoogleProvider()`.
- **`Provider` ABC** is frozen in [`backend/agents/base.py`](../backend/agents/base.py):
  `search(service, query, filters)`, `get(service, item_id)`, `execute(service, action, args)`.
- **Sync beat** [`backend/workers/sync.py`](../backend/workers/sync.py) already loops
  `_upsert_datasource → _chunk_or_fallback → embedder.embed_texts → _replace_chunks` per item; the
  `else` (non-mock) branch currently yields an **empty corpus** — that is the Phase-2 seam.
- **Write gate** lives in [`backend/orchestration/executor.py`](../backend/orchestration/executor.py):
  a node whose `tool ∈ backend.agents.WRITE_TOOLS` **suspends before the tool is called**; the real
  mutation only runs on resume in [`backend/workers/confirm.py`](../backend/workers/confirm.py).
- **Inference/embeddings** are **Gemini via an OpenAI-compat httpx client** (no `google-*` SDK), with
  a Modal/Qwen + Modal-BGE adapter alternative ([`backend/llm/client.py`](../backend/llm/client.py),
  [`backend/embeddings/embedder.py`](../backend/embeddings/embedder.py)).

**Not built — Phase 2 scope:**

- OAuth flow (`backend/api/routes_auth.py` is a 501 stub), credential encrypt/load/refresh, the three
  live per-service adapters, wiring them into `sync.py`, the `GoogleProvider` methods, three new
  `users` columns, and the new config/deps.

**One correctness note discovered during reconciliation:** `hybrid_search._SERVICES` only recognizes
the canonical tokens `gmail` / `gcal` / `gdrive`, but `DriveAgent.service == "drive"`. `GoogleProvider`
(like the mock) receives the raw agent token, so `search()` **must normalize** `drive→gdrive`,
`calendar→gcal` before delegating to `hybrid_search`. `MockProvider.get` already keeps a
`drive`/`gdrive` + `calendar`/`gcal` alias map — reuse the same normalization.

---

## 1. Interface recap (unchanged)

`Provider` ([`backend/agents/base.py`](../backend/agents/base.py)): `search(service, query, filters)`,
`get(service, item_id)`, `execute(service, action, args)`. `GoogleProvider` implements the same three.
`search()` delegates to the existing
[`backend/embeddings/search.py::hybrid_search`](../backend/embeddings/search.py) over `*_vector_store`
— **no Google call**. Only `get`/`execute` and the sync ingestion differ from `MockProvider`.

`GoogleProvider` **must accept `session` + `user_id`** (exactly like `MockProvider.__init__`) because
`search()` needs both to call `hybrid_search`, and `get`/`execute` need the session to look up the
row / write `actions_log`. Update `factory.get_provider` to construct
`GoogleProvider(session=session, user_id=user_id)` — today it calls `GoogleProvider()` with no args,
which would break the drop-in.

## 2. New dependencies

`google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `cryptography` (Fernet). Add to
`pyproject.toml` `[project.dependencies]` (and re-lock with `uv`). All are pure-Python — the CPU-only
Docker image is unaffected (ML inference stays external via Gemini/Modal).

**Lift the stub's hard rule.** [`backend/providers/google/provider.py`](../backend/providers/google/provider.py)
carries a docstring rule that **no `google-auth*` / `googleapiclient` import may ever appear** (a
Phase-1 scope convention). Phase 2 necessarily introduces them into `backend/providers/google/`; update
that docstring so the convention is scoped to "outside `providers/google/`" (the CPU-only-image intent
still holds — these deps carry no ML weights). No CI test currently greps for this, so it is a
docstring/convention fix, not a broken gate.

## 3. Config additions (`backend/config.py`)

Already present: `PROVIDER`, `SYNC_BEAT_MINUTES`. **Add only the new keys:**
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_SCOPES` (space-separated,
env-safe), `TOKEN_ENCRYPTION_KEY` (Fernet key), `DRY_RUN_WRITES` (bool, default **true**),
`SYNC_PAGE_SIZE`, `GMAIL_BATCH_SIZE` (≤100), `GOOGLE_UNITS_PER_SEC` (250 quota budget), and
**`FRONTEND_URL`** (default `http://localhost:5173`) — the SPA origin the OAuth callback redirects back
to (§4). Also document `OAUTHLIB_INSECURE_TRANSPORT=1` (env only, dev) so `google-auth-oauthlib`
accepts an `http://localhost` redirect. Mirror all new keys into `.env.example` and the compose
`x-app-environment` block. See §14 for the full secrets/URLs checklist.

## 4. OAuth 2.0 flow (`backend/api/routes_auth.py` — replace the 501 stub)

Use `google_auth_oauthlib.flow.Flow.from_client_config`. Routes mount under `settings.API_V1_STR`
(`/api/v1`) + the router prefix `/auth`, so the full paths are `/api/v1/auth/google[...]`.

- **Scopes (least privilege)**: Gmail `gmail.modify` (read + labels + drafts + send) — tighter
  alternative: `gmail.readonly` + `gmail.send`; Calendar `calendar.events`; Drive `drive.readonly` +
  `drive.file`; plus `openid`, `email`, `userinfo.email` for the callback's identity lookup.
  Escalation to document: `share_file` / `move_file` on *arbitrary existing* files needs full `drive`
  scope (a restricted scope → triggers Google app verification).
- `GET /api/v1/auth/google`: build `authorization_url(access_type='offline',
  include_granted_scopes='true', prompt='consent', state=<CSRF>)`; store `state` in Redis; redirect.
  `access_type=offline` + `prompt=consent` guarantees a **refresh_token**.
- `GET /api/v1/auth/callback`: verify `state`, `flow.fetch_token(code=...)`, fetch userinfo
  email, upsert the `users` row, store **encrypted** access + refresh token + expiry + granted scopes,
  enqueue an initial full sync (`sync_all_users.delay`), mint a normal HS256 JWT, and **302-redirect the
  browser into the SPA** at `${FRONTEND_URL}/auth/callback#token=<jwt>&email=<email>` — the JWT rides in
  the URL **fragment** (never sent to a server/log). On a bad/expired `state` it redirects with
  `#error=invalid_state` instead. It does **not** return JSON: the callback is hit by a browser redirect,
  so the SPA is the only sensible landing surface.
- **Frontend handoff (see `frontend/`)**: the React Router SPA (bearer-JWT, no cookies) adds a
  `/auth/callback` route that reads the fragment, calls `setToken()`, and lands the user in `/app`; the
  login page gets a "Sign in with Google" button that does a full-page nav to `/api/v1/auth/google`. So
  the connect flow is: SPA button → `/api/v1/auth/google` → Google consent → `/api/v1/auth/callback`
  (registered at the SPA origin; Vite proxies `:5173/api/v1` → backend) → SPA
  `/auth/callback#token=…` → authenticated. Regenerate `openapi.json` (`backend/scripts/export_openapi.py`)
  and copy it into `frontend/` after any API change.
- Note: OAuth **Testing mode** (unverified, ≤100 test users) is sufficient for the take-home; prod
  multi-tenant needs consent-screen verification, especially for `gmail.modify` / full `drive`.

## 5. Credential + token management (`backend/providers/google/credentials.py` — new)

- Tokens stored **Fernet-encrypted** in `users.google_access_token` / `google_refresh_token` (columns
  already exist; they will now hold ciphertext instead of `NULL`).
- `load_credentials(user)` → `google.oauth2.credentials.Credentials(token, refresh_token, token_uri,
  client_id, client_secret, scopes, expiry)` (decrypt first).
- Auto-refresh: on `creds.expired`, `creds.refresh(google.auth.transport.requests.Request())`, then
  persist the rotated token (re-encrypted) into the new `token_expiry`/`token_scopes` columns. Guard
  with a **per-user Redis lock** to avoid concurrent refresh races (refresh-token rotation).
- Handle `invalid_grant` (revoked/expired refresh token) → set new `users.auth_status='invalid'`, stop
  that user's sync, surface "re-consent required".

## 6. GoogleProvider (`backend/providers/google/provider.py` — implement the stub)

- `__init__(self, session=None, user_id=None)` — store both (mirror `MockProvider`).
- Lazy per-service client: `_service(name, ver)` = `build(name, ver, credentials=creds,
  cache_discovery=False)` (pin/cache the discovery doc to avoid per-call network cost).
- `search(service, query, filters)` → **normalize the service alias** (`drive→gdrive`,
  `calendar→gcal`), embed the query with the **real** embedder
  (`embedder.embed_query(query, user_id=self.user_id)` — NOT `FakeEmbedder`, so it shares the corpus
  space), then delegate to `hybrid_search(session, q_embedding, service, user_id, filters, top_k)`.
  Identical shape to Mock; proves the drop-in swap.
- `get(service, item_id)` → live full-content fetch (full email body, full event, exported file text)
  → normalized dict matching the `*_datasource` columns. Wrap the blocking client call in
  `asyncio.to_thread` so it does not stall the executor's `asyncio.gather` layer.
- `execute(service, action, args)` → **the confirmed-write branch only.** The write gate is upstream:
  the executor suspends any `tool ∈ WRITE_TOOLS` **before** `execute` is ever called (it builds the
  `actions_log` PENDING row + `pending_actions` preview itself), and `confirm.py` calls the tool on
  approve. So `execute` should: if `settings.DRY_RUN_WRITES` → record a `simulated` `actions_log` row
  and return without mutating; else perform the real Google mutation and record `executed`/`failed`.
  `action` is the agent verb (`send_email`, `draft_email`, `update_labels`, `create_event`,
  `update_event`, `delete_event`, `share_file`, `create_folder`, `move_file`) — dispatch on it.
  *(Enhancement, optional: the executor currently emits a trivial `f"Pending action: {tool}"` preview;
  a richer preview — e.g. actually creating a Gmail draft for a `send_email` gate — would live in the
  executor/provider, but is out of the baseline.)*

## 7. Per-service adapters (`backend/providers/google/{gmail,gcal,drive}.py` — new)

Each adapter provides: **(a)** a sync fetch — incremental (cursor) with a full fallback — that returns
items as **plain dicts whose keys are exactly the `*_datasource` columns** (so the existing
`_upsert_datasource(model(user_id=…, **item))` works — extra keys would raise), plus a removals list
and the next cursor; **(b)** `get_full` for `get`; **(c)** the write dispatch. Chunking + embedding is
**already handled** by `sync.py` via `chunk_gmail/gcal/gdrive` + `embedder.embed_texts` +
`_replace_chunks` — adapters do **not** embed.

### Gmail (`gmail_datasource` / `gmail_vector_store`)
- **Sync**: `users.messages.list(q=..., maxResults=SYNC_PAGE_SIZE)` paginated → `BatchHttpRequest` of
  `users.messages.get(format=full)` (batch ≤ `GMAIL_BATCH_SIZE`). **Incremental**:
  `users.history.list(startHistoryId=<cursor>)`; store the latest `historyId` as the cursor.
- **Normalize → `gmail_datasource` dict** with keys: `email_id=id`, `thread_id`,
  `sender_email_id=<From>`, `receiver_email_id=<To>`, `subject`, `content=<decoded full body>`,
  `labels=labelIds`, `sent_at`/`received_at=<internalDate>`. `chunk_gmail(subject, content)` then
  produces the embed chunks in `sync.py`.
- **Writes**: `draft_email`→`drafts.create` (safe, NOT gated), `send_email`→`messages.send` (gated),
  `update_labels`→`messages.modify` (gated).
- **`get_full`**: `messages.get(format=full)` → decode MIME parts → full body for the extractor.

### Calendar (`gcal_datasource` / `gcal_vector_store`)
- **Sync**: `events.list(calendarId=primary, singleEvents=true, timeMin/timeMax, pageToken)`.
  **Incremental** via **`syncToken`** (stored as cursor); on `410 GONE` → full resync.
- **Normalize → `gcal_datasource` dict**: `event_id`, `title=summary`, `description`, `location`,
  `start_at`, `end_at`, `attendees=[email]`. `chunk_gcal(title, description, location)` embeds;
  attendees stay metadata-only.
- **Writes**: `create_event`→`events.insert`, `update_event`→`events.patch`,
  `delete_event`→`events.delete` (all gated).
- **`get_full`**: `events.get`.

### Drive (`gdrive_datasource` / `gdrive_vector_store`)
- **Sync**: `files.list(q, fields, orderBy=modifiedTime desc, pageToken)`. **Incremental** via
  `changes.getStartPageToken` → `changes.list(pageToken)` (apply upserts + removals) → store
  `newStartPageToken` as cursor.
- **Content**: native Docs → `files.export(mimeType=text/plain)` (size-capped, truncate); binaries
  (PDF) → `files.get_media` + optional `pdfminer` extraction; skip oversized files (name-only).
- **Normalize → `gdrive_datasource` dict**: `file_id`, `name`, `mime_type`, `content=<excerpt>`,
  `owner=owners[0]`, `modified_at`. `chunk_gdrive(content)` embeds.
- **Writes**: `share_file`→`permissions.create` (gated), `create_folder`→`files.create` (safe, NOT
  gated), `move_file`→`files.update` add/removeParents (gated).

## 8. Sync worker (`backend/workers/sync.py`)

Replace the non-mock **empty-corpus** `else` branch with provider-driven ingestion. The existing loop
(`_upsert_datasource → _chunk_or_fallback(chunk_*) → embedder.embed_texts → _replace_chunks`) is
**reused as-is** — the only change is the source of `corpus` and adding removals + cursor persistence:

Per active user + service: `load_credentials` → the adapter's incremental fetch (full fallback if no
`sync_status.cursor` / on `410`) returning `(upserts, removals, next_cursor)` → run the existing
upsert/chunk/embed/`_replace_chunks` path over `upserts` → **delete removed `*_datasource` rows**
(chunks cascade) → set `sync_status.last_synced_at`, `item_count`, and **`cursor=next_cursor`** (the
column already exists; today's loop never sets it). Sync **and** index/embed happen in the one 15-min
pass, so freshly-synced items are searchable immediately.

Celery tasks are **sync**, so adapters may call the Google client directly (**no `to_thread` needed** —
a real benefit of doing bulk work in Celery), and the embed step already batches
(`GEMINI_EMBED_BATCH_SIZE`) within the pass. Backoff/quota: retry `429` / `403 rateLimitExceeded` with
exponential backoff + jitter (mirror the pattern in `backend/llm/client.py::_backoff_delay`); per-user
Redis **token bucket** against `GOOGLE_UNITS_PER_SEC`; batch to cut round-trips. Initial full sync
enqueued at first auth (§4).

## 9. Migration (`backend/migrations/versions/` — one new Alembic revision)

`sync_status.cursor` and `users.google_access_token`/`google_refresh_token` **already exist** in
`0001_initial` — do **not** re-add them. The new revision (`down_revision = "0001_initial"`) adds only:
`users += token_expiry TIMESTAMPTZ (nullable), token_scopes JSONB (nullable), auth_status TEXT
(nullable, default 'valid')`. **No vector changes, no `sync_status` changes.** Mirror the new columns
in `backend/db/models.py::User`.

## 10. Provider selection / wiring

`settings.PROVIDER` already picks `MockProvider` vs `GoogleProvider` in
[`backend/providers/factory.py`](../backend/providers/factory.py). The single wiring change is passing
context to the Google branch: `return GoogleProvider(session=session, user_id=user_id)`. Planner,
executor, agents, synthesizer, and hybrid search are unchanged. Agent tools already fetch the provider
per call via `get_provider(session=session, user_id=user_id)`.

## 11. Drawbacks (explicitly documented)

### `google-api-python-client` (chosen client lib)
- **Blocking**: every **async-executor-path** call (`get`/`get_context`, any live preview) must run in
  `asyncio.to_thread`, consuming the default threadpool; at high concurrency this bounds throughput and
  adds latency vs an async-native client. *Mitigation*: search is cache-backed (Google off the read hot
  path), all bulk work runs in the Celery sync beat (sync-native), cap concurrent live calls.
  *(Note: the query pipeline runs inside the Celery worker via `asyncio.run(pipeline(...))`, so "hot
  path" here means the executor's `asyncio.gather` layer, not FastAPI request handlers.)*
- **Discovery-doc overhead**: `build()` fetches a discovery doc → pin/cache it (`cache_discovery=False`
  + a pinned static doc) to avoid per-call network cost and an external dependency in the hot path.
- Heavier dependency footprint; batching uses its own `BatchHttpRequest` object.
- *Escape hatch if async pressure bites*: move only the executor-path calls to `aiogoogle` / thin
  httpx, keep the official client in the Celery beat.

### Poll-baseline freshness (chosen sync strategy)
- **Staleness**: up to ~15-min lag (`SYNC_BEAT_MINUTES`) — meets the <15min SLA but sits at its edge;
  an item arriving just after a tick is invisible until the next tick.
- **Idle quota cost at scale**: polling 1M users every 15 min is steady Google load even when nothing
  changed; incremental cursors keep payloads tiny, but each user still costs ≥1 list call / service /
  tick. *Mitigation*: jittered / staggered beat (avoid thundering herd), sync only active users,
  backoff.
- **Real fix = push (documented future work)**: Gmail `users.watch` → Pub/Sub topic → `POST
  /webhooks/google`; Calendar `events.watch` + Drive `changes.watch` channels (need periodic renewal +
  a public HTTPS endpoint). Cuts staleness to seconds and removes idle polling. Deferred because it
  needs Pub/Sub + a verified public webhook — out of scope for the take-home baseline.

## 12. Testing / verification

- **Unit**: normalize functions (Google JSON → `*_datasource` dict) against recorded fixtures, asserting
  keys match the model columns exactly; token refresh (expired → refreshed, `invalid_grant` →
  `auth_status='invalid'`).
- **Integration (single throwaway test account, OAuth Testing mode, `EMBED_MODE=real`)**:
  `/api/v1/auth/google` → callback → initial sync → assert `*_datasource` + `*_vector_store` populated
  → run a real query end-to-end → verify search hits real items. Seed the account with a few emails /
  events / files matching the golden set.
- **Write path**: `draft_email` creates a real Gmail draft (safe, ungated); send / create / delete
  tested with `DRY_RUN_WRITES=true` first, then one real confirm on the test account (through the
  executor suspend → `confirm.run_resume` path); verify `actions_log`.
- **Incremental**: mutate the account (self-email / add event) → `POST /api/v1/sync/trigger` → assert
  the delta appears, a removal is deleted, and `sync_status.cursor` advanced.
- **Quota / backoff**: force or mock a `429` → assert retry + backoff, no crash.
- **Contract**: the existing golden-set eval + orchestrator tests must stay green with `PROVIDER=mock`
  (same interface) — no test currently pins the `/auth/google` 501, so replacing it breaks nothing.

## 13. Implementation order

1. Deps (§2) + config keys (§3) + Fernet key + `.env.example`/compose mirroring; scope-fix the stub
   docstring; update `factory.get_provider` to pass `session`/`user_id` to `GoogleProvider`.
2. Migration (`users += token_expiry, token_scopes, auth_status`) + `User` model columns.
3. OAuth flow (`routes_auth`) + `credentials.py` (encrypt / store / load / refresh + Redis lock).
4. `GoogleProvider` skeleton: `__init__(session, user_id)` + `search` (alias-normalize + real
   `embed_query` → `hybrid_search`) to prove the drop-in swap under `PROVIDER=google, EMBED_MODE=real`.
5. **Gmail** adapter (sync normalize + `get_full` + writes) → wire the non-mock branch of `sync.py`
   (upserts + removals + cursor) → first full sync → query works on real Gmail.
6. **Calendar**, then **Drive** adapter (same pattern).
7. Incremental cursors + backoff / quota (per-user token bucket) + batch.
8. Write gate against real APIs (draft first, then gated mutations) + `DRY_RUN_WRITES`.
9. Tests + docs: README (Google Cloud project / OAuth client setup, enabled APIs, redirect URI, test
   users, `PROVIDER=google` + `EMBED_MODE=real`), DESIGN.md (push-future + drawbacks), API.md (callback
   route).

## 14. Connection URLs / keys / secrets to provision

### New for Phase 2 — Google OAuth (you must create these)

| Env var | Where it comes from | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Google Cloud Console → **APIs & Services → Credentials → Create OAuth client ID → Web application** | Public-ish, but keep with the secret. |
| `GOOGLE_CLIENT_SECRET` | Same OAuth client | **Secret** — never commit. |
| `GOOGLE_REDIRECT_URI` | You choose; **must exactly match** an "Authorized redirect URI" on the OAuth client | Dev: `http://localhost:5173/api/v1/auth/callback` (SPA origin; Vite proxies `/api/v1` → backend). Prod: `https://<host>/api/v1/auth/callback`. |
| `GOOGLE_SCOPES` | You define | e.g. `gmail.modify calendar.events drive.readonly drive.file openid email https://www.googleapis.com/auth/userinfo.email`. |
| `TOKEN_ENCRYPTION_KEY` | Generate a Fernet key | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **secret**, rotating it invalidates all stored Google tokens. |
| `DRY_RUN_WRITES` | You set | `true` for safe demos (writes simulated), `false` to hit real Google. |
| `SYNC_PAGE_SIZE`, `GMAIL_BATCH_SIZE` (≤100), `GOOGLE_UNITS_PER_SEC` (~250) | You tune | Quota/pagination budget. |
| `FRONTEND_URL` | You set | SPA origin the callback redirects back to (`http://localhost:5173` dev; the deployed SPA origin in prod). Must be the SPA that serves the `/auth/callback` route. |
| `OAUTHLIB_INSECURE_TRANSPORT=1` | Auto-set by the backend for `http://localhost` redirects | `routes_auth.py` sets it (plus `OAUTHLIB_RELAX_TOKEN_SCOPE`) automatically so `google-auth-oauthlib` accepts the dev `http://localhost` redirect. **Never applies to an https prod redirect.** |

**Google Cloud Console one-time setup (not env vars, but required):**
- Create/select a **Google Cloud project**.
- **Enable APIs**: Gmail API, Google Calendar API, Google Drive API, and People API (for userinfo).
- Configure the **OAuth consent screen** (External, **Testing** mode) and add your test account(s) as
  **Test users** (≤100).
- Create the **OAuth 2.0 Web-application client** → yields `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`;
  register the redirect URI there.

### Already required (existing infra + inference)

Per the decision to **use the Modal LLM provider instead of Google AI Studio**, Modal (Qwen chat +
BGE embeddings) is the real-inference backend for Phase 2; Gemini stays only as an optional fallback.

| Env var | Purpose | Phase-2 relevance |
|---|---|---|
| `SECRET_KEY` | JWT signing (app refuses to start without it) | unchanged |
| `DATABASE_URL` | Postgres/pgvector DSN | unchanged |
| `REDIS_URL` | Broker + progress streams + caches + **OAuth `state`** + per-user refresh lock + token buckets | now also holds OAuth `state` + refresh locks |
| `PROVIDER=google` | Selects `GoogleProvider` | **set for Phase 2** |
| `EMBED_MODE=real` | Live embeddings so corpus + query share a vector space | **set for Phase 2** (fake vectors make real Google items unsearchable) |
| **`LLM_PROVIDER=modal_qwen`** | Routes chat (classify/plan/synth/extract) to the Modal-hosted Qwen server | **the real chat backend for Phase 2** |
| **`LLM_BASE_URL`** | Modal OpenAI-compat chat endpoint (incl. `/v1` segment) | **required** for Modal chat |
| **`EMBEDDER_BASE_URL`** | Modal-hosted BGE `/embed` service (1024-dim, L2-normalized) | **required** so corpus + query embeddings come from Modal BGE |
| **`MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET`** | Modal proxy auth (`Modal-Key` / `Modal-Secret` headers), shared by chat + embedder | **required** for both Modal services |
| `LLM_MODEL` | Qwen model id (e.g. `Qwen/Qwen3.6-35B-A3B`) | Modal chat model |
| `FIRST_SUPERUSER_EMAIL` / `FIRST_SUPERUSER_PASSWORD` | Seeded superuser | unchanged |
| *(optional fallback)* `GEMINI_STUDIO_API_KEY` / `INFERENCE_BASE_URL` / `CHAT_MODEL` / `EMBED_MODEL` | Gemini AI Studio — used **only** with `LLM_PROVIDER=gemini` | not needed when Modal is configured |

> **Three independent credential sets — never conflate them:**
> - **Modal** (`LLM_BASE_URL` / `EMBEDDER_BASE_URL` / `MODAL_PROXY_TOKEN_*`) — the **inference + embedding**
>   backend (Qwen chat + BGE vectors). This is what "use the Modal LLM provider" means.
> - **Google OAuth** (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`) — **user Workspace access** (Gmail /
>   Calendar / Drive), from Google Cloud Console.
> - **Gemini AI Studio** (`GEMINI_STUDIO_API_KEY`) — a distinct API key, only if you fall back off Modal.
>
> `EMBED_MODE=fake` (the offline default) still works for local dev without any Modal endpoint; flip to
> `real` + set the Modal vars to run live.

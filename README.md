# Orca

An async orchestrator that answers natural-language questions across **Gmail,
Google Calendar, and Google Drive**. It classifies intent, plans an execution
DAG, fans out to per-service agents in parallel, runs hybrid **pgvector**
semantic search over the connected corpus, and synthesizes a natural-language
answer — built from scratch on **FastAPI + Postgres/pgvector + Redis + Celery**
with **no LangChain / LlamaIndex / agent framework** and **no managed vector DB**.

> Connects to **real Gmail, Calendar, and Drive** over Google OAuth
> (`PROVIDER=google`, `GET /api/v1/auth/google`) behind a `Provider`
> interface. Inference + embeddings are pluggable — Modal-hosted Qwen chat +
> BGE embeddings, or Google Gemini (AI Studio) via its OpenAI-compatibility
> layer — see [Live inference](#live-inference-modal--gemini).

---

## Screenshots

<p align="center">
  <img src="ss_2.png" alt="Orca sign-in screen" width="800"><br>
  <img src="ss_1.png" alt="Orca chat interface" width="800">
</p>

---

## What it does

```
"Cancel my Turkish Airlines flight"
  → search Gmail for the booking (PNR) → find the calendar event
  → draft a cancellation email → PAUSE for confirmation (write gate)

"Prepare for tomorrow's meeting with Acme Corp"
  → find the calendar event ∥ search emails with Acme ∥ pull Drive docs → synthesize

"What's on my calendar next Tuesday?"
  → resolve the timezone-correct date range → search events → format
```

---

## Architecture

```
POST /api/v1/query {query, conversation_id?, confirm?}
      │  enqueue → 202 { task_id, status:"queued", conversation_id }     ← Redis broker
      ▼
Celery task: orchestrate(task_id)                          ← progress → Redis stream · tasks row
   1. Intent Classifier   (LLM #1, JSON mode)  → Intent{ services[], intent, entities{}, steps[] }
   2. Planner             (LLM #2, JSON mode)  → Plan{ nodes[] w/ depends_on, args, optional }
   3. Executor            topo-sort · asyncio.gather parallel layers · deferred-arg extractor
        ├─ GmailAgent  search_emails / get_email / send_email* / draft_email / update_labels*
        ├─ GCalAgent   search_events / get_event / create_event* / update_event* / delete_event*
        └─ DriveAgent  search_files / get_file / share_file* / create_folder / move_file*
                       each agent → GoogleProvider → hybrid pgvector search
                       (SQL prefilter → cosine <=> → collapse-to-parent → recency decay)
        ── * write gate ─▶ dump DAG checkpoint → tasks.checkpoint · status=awaiting_confirmation · STOP
   4. Synthesizer         (LLM #3)  → { response, actions_taken, pending_actions }
      ▼
tasks.result = {...} · status=success · append user+assistant messages · publish "done" → Redis
      │
      ├─ GET /api/v1/tasks/{task_id}   polls the tasks row (status / progress / result)
      └─ WS  /ws/query                 subscribes to the Redis progress stream for task_id
```

The whole pipeline is **async**: `POST /query` only enqueues a Celery task and
returns a `task_id` in ~milliseconds; the client then **polls** `GET /tasks/{id}`
or attaches a **WebSocket** to the live progress stream. The `tasks` row is the
lifecycle source-of-truth (`queued → running → (awaiting_confirmation)? →
success | failed`).

**Design details** (1M-user scaling, caching, sharding, bottlenecks, SLOs) live
in **[DESIGN.md](DESIGN.md)** (the ER diagram is below). **Every endpoint**
(request/response shape, auth, status codes) is documented in
**[API.md](API.md)**. **10+ worked sample queries** with expected outputs are
in **[docs/sample_queries.md](docs/sample_queries.md)**.

### Tech stack

| Concern | Choice |
|---|---|
| Web framework | FastAPI (async), SQLModel over SQLAlchemy 2.x async + asyncpg |
| Vector store | Postgres + **pgvector** `vector(1024)`, HNSW `vector_cosine_ops` |
| Task queue / broker | Celery (prefork worker + one beat) over Redis |
| Cache / streams / rate-limit | Redis (query-embed + intent + conversation caches; `stream:tasks:*`; token buckets) |
| Inference + embeddings | Pluggable **Adapter pattern** (`LLMProvider`/`EmbeddingProvider`): **Modal**-hosted Qwen chat + BGE embeddings (primary), or **Google Gemini** (AI Studio) OpenAI-compat REST (fallback) — plain `httpx`/`openai` SDK, CPU-only image |
| Data plane | Google Workspace via OAuth 2.0 (`google-auth-oauthlib` + `googleapiclient`, lazy-imported) + Fernet-encrypted token storage |
| Auth | FastAPI-native JWT (PyJWT HS256) + pwdlib (Argon2 primary, bcrypt fallback) |
| Package / tooling | `uv`, `ruff`, `pytest` |

---

## Data model (ER diagram)

Everything is owned by a `user` (multi-tenant isolation via `user_id` on every
table). Each service has a canonical **`*_datasource`** record (full content,
no vectors — the source-of-truth synth reads from) and a **`*_vector_store`**
child holding one embedded **chunk** per row (denormalized `user_id` for the
no-join prefilter). Vectors are `vector(1024)`; history is `conversations` +
`messages` (one row per turn-message). Full indexing/partitioning rationale is
in [DESIGN.md](DESIGN.md#8--data-model-er-diagram).

```mermaid
erDiagram
    USERS ||--o{ CONVERSATIONS : owns
    USERS ||--o{ MESSAGES : authors
    USERS ||--o{ TASKS : owns
    USERS ||--o{ GMAIL_DATASOURCE : owns
    USERS ||--o{ GCAL_DATASOURCE : owns
    USERS ||--o{ GDRIVE_DATASOURCE : owns
    USERS ||--o{ ACTIONS_LOG : owns
    USERS ||--o{ SYNC_STATUS : owns

    GMAIL_DATASOURCE  ||--o{ GMAIL_VECTOR_STORE  : "chunked into"
    GCAL_DATASOURCE   ||--o{ GCAL_VECTOR_STORE   : "chunked into"
    GDRIVE_DATASOURCE ||--o{ GDRIVE_VECTOR_STORE : "chunked into"

    CONVERSATIONS ||--o{ MESSAGES : "has (conversation_id FK)"
    CONVERSATIONS ||--o{ TASKS : "scopes"
    CONVERSATIONS ||--o{ ACTIONS_LOG : "scopes"

    TASKS ||--o| TASKS : "parent_task_id (resume chain)"
    TASKS ||--o{ MESSAGES : "produces assistant msg"
    TASKS ||--o{ ACTIONS_LOG : "write gate"

    USERS {
        uuid   id PK
        text   email UK "NOT NULL"
        text   full_name
        text   hashed_password "NOT NULL"
        bool   is_active "DEFAULT TRUE"
        bool   is_superuser "DEFAULT FALSE"
        text   timezone
        text   google_access_token
        text   google_refresh_token
        timestamptz created_at
    }

    CONVERSATIONS {
        uuid   id PK "= conversation_id (FK target)"
        uuid   user_id FK "-> users.id"
        text   title "NULL, first-query-derived"
        timestamptz created_at
        timestamptz updated_at
    }

    MESSAGES {
        uuid   id PK
        uuid   conversation_id FK "-> conversations.id NOT NULL"
        uuid   user_id FK "-> users.id"
        text   role "user | assistant"
        text   content "query text | NL answer"
        jsonb  intent "assistant-only, NULL on user"
        jsonb  classification "assistant-only"
        jsonb  entities "assistant-only"
        jsonb  plan "assistant-only"
        jsonb  actions_taken "assistant-only"
        jsonb  pending_actions "assistant-only"
        uuid   task_id FK "-> tasks.id (NULL on user msg)"
        int    seq "order within conversation"
        timestamptz created_at
    }

    TASKS {
        uuid   id PK "= task_id, exposed to client"
        uuid   user_id FK "-> users.id"
        uuid   conversation_id FK "-> conversations.id"
        text   kind "query | confirm"
        text   status "queued|running|awaiting_confirmation|success|failed"
        jsonb  progress
        jsonb  checkpoint "dumped DAG state on suspend"
        jsonb  result "{response, actions_taken, pending_actions}"
        text   error
        uuid   parent_task_id FK "-> tasks.id (NULL)"
        timestamptz created_at
        timestamptz updated_at
    }

    GMAIL_DATASOURCE {
        uuid     id PK
        uuid     user_id FK "-> users.id"
        text     email_id "UNIQUE(user_id,email_id)"
        text     thread_id
        text     sender_email_id
        text     receiver_email_id
        text     subject
        text     content "full body"
        text     labels "text[]"
        timestamptz sent_at
        timestamptz received_at
    }

    GMAIL_VECTOR_STORE {
        uuid     id PK
        uuid     datasource_id FK "-> gmail_datasource.id NOT NULL"
        uuid     user_id "denormalized (no-join prefilter)"
        text     thread_id "denormalized"
        int      chunk_index "UNIQUE(datasource_id,chunk_index)"
        text     chunk_text
        int      token_count
        vector   embedding "vector(1024)"
    }

    GCAL_DATASOURCE {
        uuid     id PK
        uuid     user_id FK "-> users.id"
        text     event_id "UNIQUE(user_id,event_id)"
        text     title
        text     description
        text     location
        timestamptz start_at
        timestamptz end_at
        text     attendees "text[] (metadata-only)"
    }

    GCAL_VECTOR_STORE {
        uuid     id PK
        uuid     datasource_id FK "-> gcal_datasource.id NOT NULL"
        uuid     user_id "denormalized"
        int      chunk_index "UNIQUE(datasource_id,chunk_index)"
        text     chunk_text
        int      token_count
        vector   embedding "vector(1024)"
    }

    GDRIVE_DATASOURCE {
        uuid     id PK
        uuid     user_id FK "-> users.id"
        text     file_id "UNIQUE(user_id,file_id)"
        text     name
        text     mime_type
        text     content "full extracted text"
        text     owner
        timestamptz modified_at
    }

    GDRIVE_VECTOR_STORE {
        uuid     id PK
        uuid     datasource_id FK "-> gdrive_datasource.id NOT NULL"
        uuid     user_id "denormalized"
        int      chunk_index "UNIQUE(datasource_id,chunk_index)"
        text     chunk_text
        int      token_count
        vector   embedding "vector(1024)"
    }

    ACTIONS_LOG {
        uuid   id PK "= action_id on the API"
        uuid   user_id FK "-> users.id"
        uuid   conversation_id FK "-> conversations.id"
        uuid   task_id FK "-> tasks.id"
        text   tool
        jsonb  args
        text   status "executed|pending|simulated|denied|failed"
        jsonb  result
        timestamptz created_at
    }

    SYNC_STATUS {
        uuid   user_id FK "-> users.id"
        text   service
        timestamptz last_synced_at
        int    item_count
        text   cursor
    }
```

---

## Getting started

### Prerequisites

- **Docker** + Docker Compose — Postgres/pgvector + Redis (and, optionally,
  the API/worker/beat containers too — see the Docker-only alternative below)
- **[uv](https://docs.astral.sh/uv/)** — migrations, seeding, tests, and the
  host-side dev loop (`scripts/dev.sh`)
- **Node 20+ and npm** (only for the frontend SPA — see [`frontend/README.md`](frontend/README.md))

### 1 · Configure environment

```bash
cp .env.example .env
# Generate a SECRET_KEY (required — the app refuses to start without one):
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))" >> .env
```

Then set the Google OAuth and inference variables described in
[Connect your Google account](#4--connect-your-google-account) and
[Live inference](#live-inference-modal--gemini) before bringing up the app tier.

### 2 · Bring up infra

```bash
docker compose up -d postgres redis
uv sync                             # install deps into a host .venv
uv run alembic upgrade head         # pgvector ext + all tables + HNSW indexes
```

### 3 · Start the app tier — `scripts/dev.sh`

```bash
scripts/dev.sh up            # api (uvicorn --reload) + celery worker + celery beat
scripts/dev.sh status        # infra + app + frontend reachability, one table
scripts/dev.sh logs api      # tail -f one component: api | worker | beat
scripts/dev.sh down          # stop the app tier (--all also stops postgres)
```

`up` preflights Postgres/Redis (bringing Postgres up via Compose itself if
it's down), then runs `api`/`worker`/`beat` as **host** processes — not
containers — each detached into its own process group, logging to
`.dev-logs/<name>.log`. `api` runs `uvicorn --reload`, so backend edits take
effect immediately, which nothing else in this repo does for the backend (see
the Docker-only alternative below). `scripts/dev.sh --help` lists every flag.

The API is now live at **http://localhost:8000** — interactive docs at `/docs`
(Swagger UI) and `/redoc` (ReDoc), health check at `/health`.

> **Docker-only alternative (no `uv`, no host processes).** Compose can run
> `api`/`worker`/`beat` too, just without `--reload` — a backend change needs
> `docker compose up -d --build <service>` to land:
> ```bash
> docker compose up -d          # postgres, redis, api, worker, beat
> uv run alembic upgrade head   # or: docker compose exec api uv run alembic upgrade head
> ```

### 4 · Connect your Google account

Create an OAuth 2.0 Web client in Google Cloud Console with authorized
redirect URI `http://localhost:5173/api/v1/auth/callback`, then set in `.env`:

```bash
PROVIDER=google
EMBED_MODE=real   # corpus + query vectors must share a space — see Live inference
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
TOKEN_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

Restart the app tier (`scripts/dev.sh restart`), start the frontend (step 5),
and click **Sign in with Google**. The callback creates your user, stores
Fernet-encrypted tokens, and immediately enqueues a real Gmail/Calendar/Drive
sync.

Once signed in, the JWT lives in the browser's `localStorage["wso.token"]`
(see [`frontend/README.md`](frontend/README.md#authentication)) — grab it
from devtools to script against the API directly:

```bash
TOKEN=<paste the token from localStorage>

TASK=$(curl -s -XPOST localhost:8000/api/v1/query \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"What'\''s on my calendar next Tuesday?"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')

curl -s localhost:8000/api/v1/tasks/$TASK -H "authorization: Bearer $TOKEN" | python -m json.tool
```

### 5 · Run the frontend

A React Router v7 chat SPA (message thread, server-backed conversation history,
Gmail/Calendar/Drive sync status) lives in [`frontend/`](frontend/) and talks to
the API at `http://localhost:8000` (set in `frontend/.env`). With the backend up:

```bash
cd frontend
npm install
npm run dev                   # Vite dev server + HMR at http://localhost:5173
```

Build, typecheck, auth flow, and Cloudflare Workers deploy are documented in
[`frontend/README.md`](frontend/README.md).

### 6 · (optional) Retrieval evaluation

```bash
uv run python -m backend.eval.evaluate        # in-process Precision@5 + per-query latency
```

The eval harness imports the pipeline coroutine **in-process** (bypassing Celery
and HTTP) so retrieval quality (Precision@5) and search latency are measured
without queue noise; it seeds its own corpus copy, independent of step 4.
Under `EMBED_MODE=real` it asserts **Precision@5 > 0.8** and **< 500 ms**
search latency.

---

## Live inference (Modal / Gemini)

Chat + embeddings sit behind two swappable adapters — `LLMProvider`
(`backend/llm/`) and `EmbeddingProvider` (`backend/embeddings/`) — so every
call site (classifier, planner, synthesizer, embedder) is unaffected by which
backend is active:

| Backend | Chat | Embeddings | Selected by |
|---|---|---|---|
| **Modal** (`.env.example`'s recommended real backend) | Qwen (`Qwen/Qwen3.6-35B-A3B`) over an OpenAI-compatible endpoint, header-authed (`Modal-Key`/`Modal-Secret`) | Modal-hosted BGE (`POST /embed`) | `LLM_PROVIDER=modal_qwen` + `LLM_BASE_URL` / `EMBEDDER_BASE_URL` + `MODAL_PROXY_TOKEN_ID`/`_SECRET` |
| **Gemini** (AI Studio, free tier) | `gemini-2.5-flash` | `gemini-embedding-001` @ 1024 dims (OpenAI-compat `dimensions`, MRL truncation) | `LLM_PROVIDER=gemini` + `GEMINI_STUDIO_API_KEY` |

`LLM_PROVIDER=auto` (the config default) picks Modal when `LLM_BASE_URL` is
set, else Gemini. Embeddings follow the same rule independently: `EMBED_MODE=
real` routes to the Modal BGE service when `EMBEDDER_BASE_URL` is set, else
falls back to Gemini via the chat client's `embed()`.

### Gemini quick start (free, no infra to stand up)

1. Get a **free** API key at **[aistudio.google.com/apikey](https://aistudio.google.com/apikey)**
   (a Google **AI Studio** key — NOT OAuth).
2. Set in `.env`:

   ```bash
   LLM_PROVIDER=gemini
   GEMINI_STUDIO_API_KEY=your-key-here
   EMBED_MODE=real
   ```

The client reaches Gemini through its **OpenAI-compatibility layer** — one async
`httpx` client, no Google SDK, so the image stays CPU-only and dependency-light:

| Setting | Value |
|---|---|
| `INFERENCE_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` (base already includes the version → endpoints are `{BASE}chat/completions` and `{BASE}embeddings`, **not** `{BASE}/v1/…`) |
| Auth | `Authorization: Bearer ${GEMINI_STUDIO_API_KEY}` on every request |
| `CHAT_MODEL` | `gemini-2.5-flash` (free-tier flash; env-overridable) |
| `EMBED_MODEL` | `gemini-embedding-001` at **1024 dims** (OpenAI-compat `dimensions=1024`, MRL truncation) |

**Free-tier discipline.** The Gemini client and sync beat throttle to the
free-tier RPM/RPD and retry `429`/`503` with exponential backoff honoring
`Retry-After`. The Redis **query-embedding** and **intent** caches are
load-bearing for quota, not just latency. Switching between backends doesn't
re-embed anything that's already stored — **re-embed the corpus** so corpus
and query vectors share a space: `POST /api/v1/sync/trigger` re-syncs and
re-embeds your connected Google account with whichever backend is active.

---

## Environment variables

Declared in `backend/config.py` (`pydantic-settings`); `.env` is auto-loaded.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(none — required)* | JWT signing key. App raises `ValueError` at startup if unset (unless `TESTING=1`). Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/workspace_orchestrator` | Async Postgres DSN (asyncpg driver). |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker + progress streams + caches + rate-limit buckets. |
| `PROVIDER` | `google` | Real Gmail/Calendar/Drive over OAuth — set explicitly in `.env` (see [step 4](#4--connect-your-google-account)). |
| `SYNC_BEAT_MINUTES` | `15` | Background sync + embed cadence. |
| `RATE_LIMIT_PER_USER_PER_HOUR` | `100` | Per-user query rate limit (Redis token bucket). |
| `DEFAULT_TZ` | `America/New_York` | Default timezone for temporal resolution. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `11520` (8 days) | JWT lifetime — no refresh endpoint exists, so expiry means re-login. |

### Inference — chat (`LLMProvider`, `backend/llm/`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `auto` picks `modal_qwen` when `LLM_BASE_URL` is set, else `gemini`; or force one by name. |
| `LLM_BASE_URL` | *(empty)* | Modal chat endpoint, including its version segment (e.g. `https://<app>.modal.direct/v1`). |
| `LLM_API_KEY` | `unused` | The OpenAI SDK requires a non-empty key; Modal auth actually rides on the headers below. |
| `LLM_MODEL` | `Qwen/Qwen3.6-35B-A3B` | Modal chat model. |
| `LLM_MAX_TOKENS` | `2048` | Max completion tokens for the Modal adapter. |
| `LLM_REASONING_EFFORT` | `none` | Keeps Qwen3 JSON-mode output free of think traces. |
| `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` | *(empty)* | Sent as `Modal-Key` / `Modal-Secret` headers; shared with the BGE embedder below. |
| `GEMINI_STUDIO_API_KEY` | *(empty)* | AI Studio key. Required for `LLM_PROVIDER=gemini` (or embeddings falling back to Gemini). |
| `INFERENCE_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Gemini OpenAI-compat base (trailing slash; no `/v1`). |
| `CHAT_MODEL` | `gemini-2.5-flash` | Gemini chat/completions model. |
| `GEMINI_MAX_CONCURRENCY` | `4` | Outbound concurrency cap for Gemini calls. |
| `GEMINI_MAX_RETRIES` | `5` | Max `429`/`503` retries (exponential backoff + jitter); also used as the Modal adapter's `max_retries`. |

### Inference — embeddings (`EmbeddingProvider`, `backend/embeddings/`)

| Variable | Default | Purpose |
|---|---|---|
| `EMBED_MODE` | `real` | Routes to Modal BGE (if `EMBEDDER_BASE_URL` set) or Gemini for corpus/query embeddings. |
| `EMBEDDER_BASE_URL` | *(empty)* | Modal-hosted BGE `/embed` endpoint. Unset → real mode falls back to Gemini. |
| `EMBEDDER_QUERY_INSTRUCTION` | `"Represent this sentence for searching relevant passages:"` | BGE retrieval instruction applied to the **query** side only; documents embed with `None`. |
| `EMBED_MODEL` | `gemini-embedding-001` | Gemini embedding model. |
| `EMBED_DIM` | `1024` | Vector dimension (matches the `vector(1024)` schema). |
| `EMBED_QUERY_PREFIX` | *(empty)* | Generic query prefix — off by default so query/corpus embed symmetrically. |
| `GEMINI_EMBED_BATCH_SIZE` | `32` | Embedding batch size (Gemini + Modal Qwen adapters). |
| `RERANK_ENABLED` | `false` | Cross-encoder rerank stage (built but disabled; enable only if Precision@5 < 0.8). |
| `RERANK_MODEL` | `bge-reranker-v2-m3` | Reranker model, when enabled. |

### Real Google Workspace (`PROVIDER=google`)

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | *(empty)* | OAuth 2.0 Web client credentials (Google Cloud Console → APIs & Services → Credentials). |
| `GOOGLE_REDIRECT_URI` | `http://localhost:5173/api/v1/auth/callback` | Must exactly match an "Authorized redirect URI" on the OAuth client — registered at the **SPA** origin (see [`frontend/README.md`](frontend/README.md#authentication)). |
| `GOOGLE_AUTH_URI` / `GOOGLE_TOKEN_URI` | Google's standard endpoints | Rarely overridden. |
| `GOOGLE_SCOPES` | `openid email userinfo.email gmail.modify calendar.events drive.readonly drive.file` | Space-separated at rest. |
| `TOKEN_ENCRYPTION_KEY` | *(empty — required for `PROVIDER=google`)* | Fernet key encrypting stored Google tokens. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `DRY_RUN_WRITES` | `true` | Writes (send email, create/delete event, …) are simulated and logged, never executed, until set `false`. |
| `SYNC_LOOKBACK_DAYS` | `7` | First (cursor-less) full-sync window for Gmail/Drive; later passes are incremental. |
| `SYNC_PAGE_SIZE` / `GMAIL_BATCH_SIZE` | `100` / `50` | Pagination sizes for the Google list APIs. |
| `GOOGLE_UNITS_PER_SEC` | `250` | Client-side throttle matching Google's per-project quota. |
| `FRONTEND_URL` | `http://localhost:5173` | SPA origin the OAuth callback redirects back to (JWT handed off in the URL fragment). |

---

## Running the tests

The suite is deliberately minimal and **hermetic-first** — the classifier/planner
JSON-contract tests stub the LLM and embedder with deterministic recorded
fixtures, so they need **no** network and **no** API key.

```bash
# Tier 1 — hermetic (default; CI-safe, no network)
TESTING=1 SECRET_KEY=test-secret-key-32-bytes-minimum-len \
  uv run pytest -m "not llm" -q

# Tier 2 — live contract tests against real Gemini (opt-in)
GEMINI_STUDIO_API_KEY=your-key EMBED_MODE=real \
  uv run pytest -m llm -q

# Lint
uv run ruff check backend tests
```

`TESTING=1` lets the config accept a test `SECRET_KEY` without a real one. Tests
assert **structural invariants** (which `services` fire, DAG shape/deps, deferred
`$nX.field` refs, `needs_clarification` gating, `tool ∈ registry`) plus the one
deterministic exact value (the tz-resolved "Next Tuesday" range) — never free-text
prose, because an LLM is not byte-deterministic even at `temperature=0`.

---

## Regenerating the OpenAPI spec

The committed **[`openapi.json`](openapi.json)** is the version-controlled API
contract (also served live at `/openapi.json`, `/docs`, `/redoc`):

```bash
TESTING=1 SECRET_KEY=test-secret-key-32-bytes-minimum-len \
  uv run python backend/scripts/export_openapi.py
```

The export is deterministic — running it twice produces an identical file.

---

## Project layout

```
backend/
  main.py                 FastAPI app + router/ws wiring + OpenAPI metadata
  config.py               pydantic-settings (all env keys)
  core/security.py        JWT + pwdlib password hashing (Argon2/bcrypt)
  crud.py                 user CRUD + authenticate (timing-attack guard)
  db/ models.py session.py   SQLModel tables (async) + dual engines (pooled + NullPool)
  migrations/             Alembic (pgvector ext + vector(1024) + HNSW indexes)
  llm/                    LLMProvider port (base.py) · Gemini OpenAI-compat client ·
                          Modal Qwen adapter · JSON parse/validate/repair + prompts
  embeddings/             EmbeddingProvider port · embedder (Modal-BGE/Gemini + Redis cache) ·
                          chunkers · hybrid search · reranker
  orchestration/
    models/               Intent · Plan/Node · ProgressEvent · TaskResult · Checkpoint
    stages/               classifier · planner (+ JSON-contract test fixtures)
    executor.py           topo-sort · asyncio.gather · deferred args · write-gate suspend
    utils/                tools registry · checkpoint · temporal resolver
  agents/                 gmail · gcal · drive (register tools) + WRITE_TOOLS gate set
  providers/ google/      GoogleProvider: OAuth credential store (Fernet-encrypted) +
                          per-service gmail/gcal/drive adapters (googleapiclient, lazy-imported)
  synth/ synthesizer.py   aggregate node outputs → TaskResult
  context/ features/      conversation context · conflict detection
  workers/                celery_app · orchestrate · confirm · sync (15-min beat)
  api/                    routes_{query,tasks,login,users,auth,sync,conversations} · ws · deps
  eval/                   golden set + in-process Precision@5 + latency
  scripts/                seed.py · seed_users.py · export_openapi.py
tests/                    hermetic harness (deterministic embedder + stub_llm + conftest)
frontend/                 React Router v7 SPA (Vite · TanStack Query · shadcn/Tailwind v4)
  app/                    routes (incl. Google OAuth callback) · lib/{api,auth,chat,history,sync} ·
                          components/{chat,history,status,ui} · typed from openapi.json
                          (npm run gen:api) — see frontend/README.md
scripts/                  repo-root dev tooling (distinct from backend/scripts/ above):
                          dev.sh (host dev loop — see Getting started) · verify_live.sh
                          (opt-in live-model QA) · seed.py / export_openapi.py (thin
                          wrappers around the backend/scripts/ modules above)
docker-compose.yml  Dockerfile  alembic.ini  openapi.json
README.md  DESIGN.md  API.md  docs/sample_queries.md
```

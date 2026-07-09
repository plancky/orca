# Agentic Google Workspace Orchestrator

An async orchestrator that answers natural-language questions across **Gmail,
Google Calendar, and Google Drive**. It classifies intent, plans an execution
DAG, fans out to per-service agents in parallel, runs hybrid **pgvector**
semantic search over a seeded corpus, and synthesizes a natural-language answer —
built from scratch on **FastAPI + Postgres/pgvector + Redis + Celery** with **no
LangChain / LlamaIndex / agent framework** and **no managed vector DB**.

> Phase 1 runs against a deterministic **mock corpus** behind a `Provider`
> interface (real Google OAuth is a Phase-2 stub, `GET /auth/google` → `501`).
> Inference + embeddings are **Google Gemini** via its OpenAI-compatibility layer,
> operated on the **free tier** — and fully faked (`EMBED_MODE=fake`) so the whole
> system runs offline with no API key.

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
                       each agent → Provider (MockProvider) → hybrid pgvector search
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

**Design details** (1M-user scaling, caching, sharding, bottlenecks, ER diagram,
SLOs) live in **[DESIGN.md](DESIGN.md)**. **Every endpoint** (request/response
shape, auth, status codes) is documented in **[API.md](API.md)**. **10+ worked
sample queries** with expected outputs are in
**[docs/sample_queries.md](docs/sample_queries.md)**.

### Tech stack

| Concern | Choice |
|---|---|
| Web framework | FastAPI (async), SQLModel over SQLAlchemy 2.x async + asyncpg |
| Vector store | Postgres + **pgvector** `vector(1024)`, HNSW `vector_cosine_ops` |
| Task queue / broker | Celery (prefork worker + one beat) over Redis |
| Cache / streams / rate-limit | Redis (query-embed + intent + conversation caches; `stream:tasks:*`; token buckets) |
| Inference + embeddings | **Google Gemini** (AI Studio) via OpenAI-compat REST — plain `httpx`, CPU-only image |
| Auth | FastAPI-native JWT (PyJWT HS256) + pwdlib (Argon2 primary, bcrypt fallback) |
| Package / tooling | `uv`, `ruff`, `pytest` |

---

## Quick start

### Prerequisites

- **Docker** + Docker Compose (Postgres/pgvector + Redis + API + worker + beat)
- **[uv](https://docs.astral.sh/uv/)** (for host-side migrations, seeding, tests)
- **Node 20+ and npm** (only for the frontend SPA — see [`frontend/README.md`](frontend/README.md))

### 1 · Configure environment

```bash
cp .env.example .env
# Generate a SECRET_KEY (required — the app refuses to start without one):
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))" >> .env
```

`EMBED_MODE=fake` (the default) needs **no** API key — a deterministic
`FakeEmbedder` produces reproducible 1024-dim vectors so the entire system,
including semantic search, works **fully offline**. See
[Gemini setup](#gemini-setup-optional--for-live-inference) to switch on live
inference.

### 2 · Bring up the stack

```bash
docker compose up -d          # postgres, redis, api, worker, beat
uv sync                       # install deps into a host .venv (for the CLI steps below)
```

The API is now live at **http://localhost:8000** — interactive docs at
**http://localhost:8000/docs** (Swagger UI) and **/redoc** (ReDoc).

### 3 · Migrate + seed

```bash
uv run alembic upgrade head                   # pgvector ext + all tables + HNSW indexes
uv run python backend/scripts/seed.py         # first superuser + mock corpus (idempotent)
```

> The migration and seed talk to Postgres at `localhost:5432` (the compose
> default). They read `.env` for `SECRET_KEY`, `DATABASE_URL`, and
> `FIRST_SUPERUSER_*`. You can equivalently run them inside the container:
> `docker compose exec api uv run alembic upgrade head`.

### 4 · Smoke-test a query

```bash
# Sign up + log in (form-encoded OAuth2 password flow)
curl -s -XPOST localhost:8000/api/v1/users/signup \
  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"hunter2hunter2","full_name":"Me"}'

TOKEN=$(curl -s -XPOST localhost:8000/api/v1/login/access-token \
  -d 'username=me@example.com&password=hunter2hunter2' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# Enqueue a query → get a task_id (HTTP 202)
TASK=$(curl -s -XPOST localhost:8000/api/v1/query \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"Find emails from sarah@company.com about the budget"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')

# Poll until success
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

Build, typecheck, and Cloudflare Workers deploy are documented in
[`frontend/README.md`](frontend/README.md).

### 6 · (optional) Retrieval evaluation

```bash
uv run python -m backend.eval.evaluate        # in-process Precision@5 + per-query latency
```

The eval harness imports the pipeline coroutine **in-process** (bypassing Celery
and HTTP) so retrieval quality (Precision@5) and search latency are measured
without queue noise. Under `EMBED_MODE=fake` it runs and prints the metrics
table; under live Gemini (`EMBED_MODE=real`) it asserts **Precision@5 > 0.8** and
**< 500 ms** search latency.

---

## Gemini setup (optional — for live inference)

Phase 1 runs offline with `EMBED_MODE=fake`. To use **real** Gemini inference and
embeddings:

1. Get a **free** API key at **[aistudio.google.com/apikey](https://aistudio.google.com/apikey)**
   (a Google **AI Studio** key — NOT OAuth).
2. Put it in `.env` and flip the embed mode to live:

   ```bash
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

**Free-tier discipline.** The LLM client, embedder, and sync beat throttle to the
free-tier RPM/RPD and retry `429`/`503` with exponential backoff honoring
`Retry-After`. The Redis **query-embedding** and **intent** caches are load-bearing
for quota, not just latency. When you switch to `EMBED_MODE=real`, **re-embed the
corpus** so corpus and query vectors share a space:

```bash
EMBED_MODE=real uv run python backend/scripts/seed.py     # or POST /api/v1/sync/trigger
```

---

## Environment variables

Declared in `backend/config.py` (`pydantic-settings`); `.env` is auto-loaded.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | *(none — required)* | JWT signing key. App raises `ValueError` at startup if unset (unless `TESTING=1`). Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/workspace_orchestrator` | Async Postgres DSN (asyncpg driver). |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker + progress streams + caches + rate-limit buckets. |
| `EMBED_MODE` | `fake` | `fake` = offline deterministic `FakeEmbedder`; `real` = live Gemini embeddings. |
| `GEMINI_STUDIO_API_KEY` | *(empty)* | AI Studio key. Required only for `EMBED_MODE=real` / live chat. |
| `INFERENCE_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Gemini OpenAI-compat base (trailing slash; no `/v1`). |
| `CHAT_MODEL` | `gemini-2.5-flash` | Chat/completions model. |
| `EMBED_MODEL` | `gemini-embedding-001` | Embedding model. |
| `EMBED_DIM` | `1024` | Vector dimension (matches the `vector(1024)` schema). |
| `EMBED_QUERY_PREFIX` | *(empty)* | BGE query prefix — **off** for Gemini (symmetric query/corpus embedding). |
| `RERANK_ENABLED` | `false` | Cross-encoder rerank stage (built but disabled; enable only if Precision@5 < 0.8). |
| `GEMINI_MAX_CONCURRENCY` | `4` | Outbound concurrency cap for Gemini calls. |
| `GEMINI_EMBED_BATCH_SIZE` | `32` | Embedding batch size. |
| `GEMINI_MAX_RETRIES` | `5` | Max `429`/`503` retries (exponential backoff + jitter). |
| `SYNC_BEAT_MINUTES` | `15` | Background sync + embed cadence. |
| `RATE_LIMIT_PER_USER_PER_HOUR` | `100` | Per-user query rate limit (Redis token bucket). |
| `DEFAULT_TZ` | `America/New_York` | Default timezone for temporal resolution. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `11520` (8 days) | JWT lifetime. |
| `FIRST_SUPERUSER_EMAIL` / `FIRST_SUPERUSER_PASSWORD` | `admin@example.com` / *(empty)* | Seeded by `backend/scripts/seed.py` (idempotent). |
| `PROVIDER` | `mock` | `mock` (seeded corpus) or `google` (Phase-2 stub). |

---

## Running the tests

The suite is deliberately minimal and **hermetic-first** — the classifier/planner
JSON-contract tests stub the LLM with recorded fixtures and use the
`FakeEmbedder`, so they need **no** network and **no** API key.

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
  llm/                    OpenAI-compat client + JSON parse/validate/repair + prompts
  embeddings/             embedder (+cache+fake) · chunkers · hybrid search · reranker
  orchestration/
    models/               Intent · Plan/Node · ProgressEvent · TaskResult · Checkpoint
    stages/               classifier · planner (+ JSON-contract test fixtures)
    executor.py           topo-sort · asyncio.gather · deferred args · write-gate suspend
    utils/                tools registry · checkpoint · temporal resolver
  agents/                 gmail · gcal · drive (register tools) + WRITE_TOOLS gate set
  providers/ mock/ google/    seeded corpus + MockProvider · Google stub (Phase 2)
  synth/ synthesizer.py   aggregate node outputs → TaskResult
  context/ features/      conversation context · conflict detection
  workers/                celery_app · orchestrate · confirm · sync (15-min beat)
  api/                    routes_{query,tasks,login,users,auth,sync,conversations} · ws · deps
  eval/                   golden set + in-process Precision@5 + latency
  scripts/                seed.py · seed_users.py · export_openapi.py
tests/                    hermetic harness (FakeEmbedder + stub_llm + conftest)
frontend/                 React Router v7 SPA (Vite · TanStack Query · shadcn/Tailwind v4)
  app/                    routes · lib/{api,auth,chat,history} · components/{chat,history,status,ui}
                          typed from openapi.json (npm run gen:api) — see frontend/README.md
docker-compose.yml  Dockerfile  alembic.ini  openapi.json
README.md  DESIGN.md  API.md  docs/sample_queries.md
```

---

## Scope (Phase 1)

**In:** the full async orchestration pipeline, hybrid pgvector search over a
mock corpus, JWT auth + user management, the write-gate suspend/resume flow, the
15-min sync+embed beat, the three bonuses (conversation context, conflict
detection, WebSocket progress), the `GET /conversations` history endpoints, and a
**React Router v7 SPA** frontend ([`frontend/`](frontend/)).

**Out (by design):** real Google OAuth / `googleapiclient` (Phase 2 —
`GET /auth/google` → `501`, `GoogleProvider` is a stub). No `google-*` SDK,
LangChain, or managed vector DB appears anywhere.

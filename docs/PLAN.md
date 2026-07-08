# Plan: Agentic Google Workspace Orchestrator

## Context

This is a greenfield take-home (`/home/planck/dev/alphalaw/google-assistant`, only the assignment file
present). Goal: an orchestrator that takes a natural-language query, classifies intent, plans an execution
DAG, fans out to Gmail/GCal/Drive agents in parallel, does semantic + hybrid search over pgvector, and
synthesizes a natural-language answer. Rubric weights **orchestration logic, embedding quality, and scaling
design** — not Google API plumbing. Restrictions: **no LangChain/LlamaIndex/agent frameworks, no managed
vector DBs** — everything from scratch on FastAPI + Postgres/pgvector + Redis + Celery. Time budget 6–8h.

**Deviation from the sample stack:** inference is **self-hosted and OpenAI-compatible** — one client points
at `INFERENCE_BASE_URL` for both `/v1/chat/completions` and `/v1/embeddings`. Embedding model is BGE/GTE-large
(**1024 dims**, so the schema is `vector(1024)`, not the sample's 1536).

### Locked decisions

| Decision | Choice | Consequence |
|---|---|---|
| Data layer | Mock seeded corpus behind a `Provider` interface | Real Google swappable later; no OAuth in the critical path |
| Orchestration | Hybrid: LLM emits typed DAG → executor runs it → bounded re-plan/clarify on gaps | Robust to "which John?" without unbounded agent loops |
| Structured output | Prompt + JSON mode, parse→validate→repair→retry (Pydantic v2) | No reliance on constrained decoding / tool-calling |
| Embeddings | BGE/GTE-large @ 1024 | `vector(1024)`; BGE query instruction prefix wired in |
| Reranking | Hybrid first; add `bge-reranker-v2-m3` only if golden-set Precision@5 < 0.8 | Keeps latency low by default |
| Inference infra | External via env (`INFERENCE_BASE_URL`, `CHAT_MODEL`, `EMBED_MODEL`) | App docker-compose stays CPU-only |
| Bonuses | Conversation context, Conflict detection, WebSocket (+ Docker as required infra) | — |

## Architecture

```
FastAPI  /api/v1/query  /auth/google(stub)  /sync/trigger  /sync/status   +  WS /ws/query (stream)
      │
      ▼
Intent + Plan  (1 LLM call, JSON mode → typed DAG)         ← self-hosted /v1/chat/completions
      │            + conversation context (last 5 turns), current datetime + user timezone injected
      ▼
DAG Executor   asyncio fan-out · topo-ordered deps · deferred arg resolution · bounded re-plan · fallback
   ├─ GmailAgent   search_emails / get_email / send_email* / draft_email / update_labels*
   ├─ GCalAgent    search_events / get_event / create_event* / update_event* / delete_event*   (* = write-gated)
   └─ DriveAgent   search_files / get_file / share_file* / create_folder* / move_file*
      │                       each agent → Provider interface (MockProvider now, GoogleProvider stub)
      ▼
Hybrid Search  metadata prefilter (SQL) → pgvector cosine → [optional rerank] → recency decay
      │                                                       ← self-hosted /v1/embeddings (batched, cached)
      ▼
Response Synthesizer (1 LLM call) → NL answer + actions_taken + pending_actions (writes await confirm)
```

Cross-cutting: **Redis** (embedding cache 1h, intent/plan cache, conversation context, sync timestamps,
rate-limit token buckets) · **Celery** worker for the 15-min background sync + embedding backfill.

## Repo layout

```
app/
  main.py               FastAPI app + router/ws wiring
  config.py             pydantic-settings (DB, Redis, INFERENCE_BASE_URL, CHAT_MODEL, EMBED_MODEL,
                        RERANK_MODEL, EMBED_DIM=1024, DEFAULT_TZ, rate limits)
  db/  models.py  session.py                SQLModel (table=True) on async SQLAlchemy 2.x + asyncpg
  migrations/           Alembic (target_metadata = SQLModel.metadata; pgvector ext; vector(1024) + indexes)
  llm/ client.py        OpenAI-compatible async client (httpx); chat() + embed()
       json_utils.py    extract-JSON + Pydantic validate + one repair reprompt + retry
       prompts/         planner, extractor, synthesizer, clarify templates
  embeddings/ embedder.py (BGE prefix + Redis cache + batch)  reranker.py  search.py (hybrid)
  orchestration/ dag.py (Plan/Node schemas)  planner.py  executor.py  tools.py (registry)
  agents/ base.py (Provider iface + BaseAgent)  gmail.py  gcal.py  drive.py
  providers/ mock/ seed.py (corpus) + mock_provider.py     google/ provider.py (stub)
  synth/ synthesizer.py
  context/ conversation.py      features/ conflict.py
  workers/ celery_app.py  sync.py
  api/ routes_query.py  routes_auth.py  routes_sync.py  ws.py  deps.py (rate limit, user)
  eval/ golden_set.json  evaluate.py   (Precision@5 + latency)
scripts/ seed.py
docker-compose.yml   README.md  DESIGN.md  API.md  openapi.json  postman_collection.json  tests/
```

## Data model & migrations

**ORM: SQLModel** (Pydantic + SQLAlchemy in one). Tables are `table=True` classes in `db/models.py`; a
`*Base` per entity is reused for API request/response schemas (SQLModel's `HeroBase → Create/Public` pattern),
so there's no separate Pydantic DTO layer. **Async** via `create_async_engine` + `AsyncSession`
(`sqlmodel.ext.asyncio.session`, so `session.exec()` still works); FastAPI `get_session()` dependency yields
the async session. Relationships declare `sa_relationship_kwargs={"lazy": "selectin"}` to stay async-safe.
**pgvector column**: `embedding: list[float] = Field(sa_column=Column(Vector(1024)))`. Hybrid search stays
Core/`text()` on the same `AsyncSession` — cosine (`embedding <=> :q` / `Vector.cosine_distance()`) is
unchanged. Alembic sets `target_metadata = SQLModel.metadata`; a bootstrap path can `conn.run_sync(
SQLModel.metadata.create_all)` for quick starts. Extend the assignment schema; embeddings become `vector(1024)`.

- **users**(id, email, timezone, google_access_token, google_refresh_token, created_at)
- **conversations**(id, user_id→users, conversation_id, query, intent JSONB, plan JSONB, response TEXT,
  entities JSONB, created_at) — powers "last 5 turns" context.
- **gmail_cache**(id, user_id, email_id, thread_id, sender, subject, body_preview, labels[],
  embedding vector(1024), received_at, UNIQUE(user_id,email_id))
- **gcal_cache**(id, user_id, event_id, title, description, location, start_at, end_at, attendees[],
  embedding vector(1024), UNIQUE(user_id,event_id))
- **gdrive_cache**(id, user_id, file_id, name, mime_type, content_excerpt, owner, modified_at,
  embedding vector(1024), UNIQUE(user_id,file_id))
- **actions_log**(id, user_id, conversation_id, tool, args JSONB, status[executed|pending|simulated|failed],
  result JSONB, created_at) — audit + the write-confirmation gate.
- **sync_status**(user_id, service, last_synced_at, item_count).

Indexes: HNSW `vector_cosine_ops` per `*_cache.embedding` (fallback IVFFlat, matching the sample), plus
btree on (user_id, received_at/start_at/modified_at) and on sender/attendees for the metadata prefilter.
All queries scoped `WHERE user_id = …`; DESIGN.md describes partition-by-user_id for scale.

## Component design

**Inference client (`llm/client.py`).** Async httpx to `{BASE_URL}/v1/chat/completions` and `/v1/embeddings`,
`CHAT_MODEL`/`EMBED_MODEL` from config, dummy API key allowed. `chat(messages, response_format=json_object,
temperature=0)`; `embed(texts: list)` batched (~64). Timeout + retry-with-backoff (Google-APIs-fail-often
hint applied to the model server too).

**JSON-mode robustness (`llm/json_utils.py`).** Since we don't have constrained decoding: strip code fences,
extract the outermost JSON object, `Model.model_validate`; on failure, one **repair reprompt** ("your last
output was invalid JSON for schema X, return only valid JSON"), then retry once more; raise typed error →
executor degrades gracefully.

**Embedder + search (`embeddings/`).** Embed text: email = `subject + "\n" + body_preview`; event =
`title + description + location`; file = `name + content_excerpt`. BGE query prefix
(`"Represent this sentence for searching relevant passages:"`) on the query side only, configurable. Redis
cache key `sha256(text)|model`, 1h TTL. **Hybrid search:** planner emits metadata filters (sender, date
range, attendee, mime, service) → SQL prefilter → `ORDER BY embedding <=> :q` cosine → optional rerank →
apply recency decay `score * exp(-λ·age)`. Thread chunking: store per-message rows; thread represented by
most-recent message + subject (documented as the chunking strategy; deeper chunking noted as enhancement).

**Agents + providers.** `Provider` interface: `search(service, query, filters)`, `get(service, id)`,
`execute(service, action, args)`. `MockProvider` reads the seeded corpus and runs the same hybrid search;
`execute` on a **write** returns `{status:"pending_confirmation", preview}` and logs to `actions_log` (never
mutates in mock). `GoogleProvider` is a stubbed skeleton showing where OAuth/googleapiclient calls slot in.
Each agent (`gmail/gcal/drive`) exposes `search()`, `get_context()`, `execute()`; tools registered in
`tools.py` as `"gmail.search_emails" → coro`.

**Planner (`orchestration/planner.py` + `dag.py`).** One LLM call, JSON mode, returns:
```
Plan{ intent, services[], entities{}, needs_clarification, clarification?, nodes:[Node] }
Node{ id, tool, args{}, depends_on[], optional, on_missing? }
```
Prompt injects: current datetime, user timezone, available tools + arg schemas, last-5-turn context, and
few-shot examples covering single/multi-service + hard cases. `args` may contain refs like `"$n1.booking_ref"`.

**Executor (`orchestration/executor.py`).** Topo-sort → run independent nodes with `asyncio.gather`.
Resolve dependent args two ways: (a) direct substitution of `$nX.field` from upstream JSON output; (b)
**deferred extraction** — when a downstream node needs a value not directly present (e.g., booking ref from
an email body), call a small extractor LLM over the upstream `get_context()` result. Failure handling:
`optional` nodes that fail are skipped (graceful degradation — "Gmail ok, Calendar failed" flows to synth);
a required node returning empty/ambiguous triggers **≤1 bounded re-plan** with partial state, or emits a
**clarification** question ("which John?") instead of guessing. Hard recursion cap prevents loops.

**Write-confirmation gate.** `draft_email`/`create_folder` treated as safe; `send_email`, `delete_event`,
`update_event`, `share_file`, `move_file`, `update_labels` return `pending_actions` in the response. A
follow-up `/query` (or dedicated confirm) with `conversation_id` + `action_id` executes (mock: marks
`actions_log.status = simulated/executed`). Mirrors the sample "Would you like me to send it?".

**Synthesizer (`synth/`).** One LLM call over aggregated node outputs → NL answer + structured
`actions_taken` + `pending_actions`. Includes the ✓-style summary from the sample.

**Conversation context (`context/conversation.py`).** Persist each turn to `conversations`; keep a rolling
last-5 (query, intent, resolved entities, result summary) in Redis per `conversation_id`; inject a compact
block into the planner to resolve "that email about the proposal".

**Conflict detection (`features/conflict.py`).** Given events + a time window (or a Drive-doc-derived OOO
window via extractor), compute interval overlaps. Powers "events next week that conflict with my OOO doc".

**WebSocket (`api/ws.py`).** `/ws/query` reuses the orchestrator with a progress callback, streaming
`node_started/node_finished/partial` events, final message = synthesized response.

**Temporal reasoning.** `users.timezone` + a `resolve_timeframe` util turns "next week"/"next Tuesday" into
explicit date ranges (Mon–Sun default, configurable) using current datetime + tz injected into the planner;
ranges become exact metadata filters.

**Celery sync (`workers/`).** Beat every 15 min per active user (+ manual `/sync/trigger`): fetch new/changed
items (mock: refresh seed deltas) → batch-embed → upsert `*_cache` → update `sync_status`. Exposes freshness
lag. **Rate limiting** (`api/deps.py`): Redis token bucket 100 queries/user/hr; DESIGN.md covers Google's
250 units/s batching.

## API endpoints

- `POST /api/v1/query` `{query, conversation_id, confirm?:{action_id}}` → `{response, actions_taken,
  pending_actions, conversation_id}`
- `GET /api/v1/auth/google` → stubbed OAuth flow (documented, not wired to live Google)
- `POST /api/v1/sync/trigger` → enqueue Celery sync
- `GET /api/v1/sync/status` → per-service last-sync timestamps + counts
- `WS /ws/query` → streamed orchestration progress
OpenAPI auto-generated by FastAPI; Postman collection exported.

## Scaling design (DESIGN.md)

LB → N FastAPI → Redis + Postgres/pgvector → Celery → Google/LLM. Strategies: Redis caching (embeddings 1h,
intent, context; target >80% hit), rate limiting (100/user/hr + Google 250 units/s batching), async Celery
for 2–5s orchestrations, background pre-compute sync (<15min freshness), **shard/partition by user_id**
(Postgres declarative partitioning; note Citus as horizontal option), multi-region US/EU/APAC nearest-route.
Metrics: P99 <2s, cache hit >80%, API errors <0.1%, freshness <15min. Include ER diagram.

## Evaluation harness (`eval/`)

`golden_set.json`: 12–15 queries (single + multi + hard cases) → expected relevant item ids over the seed
corpus. `evaluate.py` computes **Precision@5** and per-query **latency** (<500ms search target). If P@5 < 0.8,
enable the `bge-reranker` stage. This is what makes the graded targets measurable.

## Deliverables

Working code + README (setup/run) + DESIGN.md (scaling, above) + API.md + Alembic migrations + ER diagram +
10+ sample queries with expected outputs (incl. edge cases) + OpenAPI/Postman + docker-compose (Postgres+
pgvector, Redis, API, Celery worker; model endpoints external via env). Video demo left to the user to record.

## Implementation order (time-boxed 6–8h)

1. **Skeleton**: config, docker-compose, DB models, Alembic migration (pgvector + `vector(1024)`), seed script + corpus.
2. **Inference + embeddings**: OpenAI-compatible client, JSON parse/repair, embedder + Redis cache.
3. **Hybrid search + eval harness** → Precision@5 measurable early.
4. **Agents + MockProvider + tools registry**.
5. **Planner (JSON mode) + Executor** (parallel, deps, deferred extraction, re-plan, fallback) + write gate.
6. **Synthesizer + `/query`**: single-service first, then multi-service.
7. **Bonuses**: conversation context, conflict detection, WebSocket.
8. **Docs + OpenAPI/Postman + tests + sample-query outputs**.

## Verification

- `docker compose up` → run migrations → `python scripts/seed.py`.
- `python -m app.eval.evaluate` → assert Precision@5 > 0.8 and search latency < 500ms; enable reranker if short.
- Curl the sample queries: single ("calendar next week", "emails from sarah about budget", "PDFs last month"),
  multi ("cancel Turkish Airlines flight", "prepare for Acme meeting"), hard ("move the meeting with John" →
  clarification, "that email about the proposal" → context resolution, "next Tuesday" → tz-correct range).
- **Graceful degradation**: force one agent to raise → confirm partial results + note flow through synth.
- **Write gate**: mutating query returns `pending_actions`; follow-up confirm marks `actions_log` executed.
- Conflict query returns overlaps; `/ws/query` streams progress; `pytest` green.

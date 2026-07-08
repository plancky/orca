# Live-Inference Verification (Wave F5)

Everything in the hermetic suite runs with **no model server** (`FakeEmbedder` +
`stub_llm`). A handful of checks genuinely need a **real** model — a real
synthesized `/query` answer, `Precision@5 > 0.8`, `< 500 ms` search latency, and
the Tier-2 `-m llm` contract tests. Those are **opt-in** and run only when a
Gemini key is present.

Backend inference + embeddings are **Google Gemini via its OpenAI-compatibility
layer**, keyed by a single env var **`GEMINI_STUDIO_API_KEY`** (a Google AI
Studio API key — **not** OAuth), operated under **free-tier** limits.

- **Script:** [`scripts/verify_live.sh`](../scripts/verify_live.sh) — the opt-in
  gate that automates the four checks below.
- **Design intent:** `.omo/plans/backend-orchestrator.md` todo **F5**;
  `docs/PLAN.md` §Verification (l.906-949).

---

## The opt-in gate (what actually runs)

`scripts/verify_live.sh` reads `GEMINI_STUDIO_API_KEY` from the environment **at
runtime only** (it is never hardcoded, never baked into an image):

| `GEMINI_STUDIO_API_KEY` | Behaviour | Exit code |
|---|---|---|
| **unset** | Prints this runbook, **asserts nothing**, spends no quota. | `0` |
| **set** | `export EMBED_MODE=real`, runs the four checks in order, prints `PASS`/`FAIL` per step. | number of failed steps (`0` = all green) |

**Precision@5 and the `< 500 ms` latency bound are HARD gates ONLY when the key
is present.** Hermetically (no key) they are soft, documented checks — the
`evaluate.py` harness still runs and prints the numbers, but does not fail the
build on them. This mirrors the plan's "soft gate by default, hard gate when the
Gemini key is present."

---

## 1. Get a free Gemini key

1. Open **https://aistudio.google.com/apikey** and sign in with a Google account.
2. Click **Create API key** (free tier — no billing required).
3. Export it (do **not** commit it; `.env` is gitignored):

   ```bash
   export GEMINI_STUDIO_API_KEY=<your-free-key>
   ```

The key is sent as `Authorization: Bearer ${GEMINI_STUDIO_API_KEY}` to the
OpenAI-compat base `https://generativelanguage.googleapis.com/v1beta/openai/`
(the base already includes the version → endpoints are `{BASE}chat/completions`
and `{BASE}embeddings`). Models default to `gemini-2.5-flash` (chat) and
`gemini-embedding-001` (embeddings) and are env-overridable via `CHAT_MODEL` /
`EMBED_MODEL`.

## 2. Free-tier caveats (read before running)

- **RPM / RPD limits.** The free tier caps requests-per-minute and
  requests-per-day. A full live run re-embeds the corpus **and** runs the golden
  set **and** the sample queries — keep the **golden set small (≤ 15 queries)**
  and throttled so a full run stays within the daily quota.
- **The client already backs off on `429`.** `LLMClient` / the embedder retry
  `429`/`503` with exponential backoff + jitter, honoring any `Retry-After`,
  bounded by `GEMINI_MAX_RETRIES`, and cap outbound concurrency at
  `GEMINI_MAX_CONCURRENCY`. You do not need to add sleeps — but if you hit the
  **daily** cap, wait for the quota window to reset.
- **The Redis caches are load-bearing for quota**, not just latency: the
  per-user query-embedding cache (`user:{uid}:emb:{sha256(text)}|model`, 1 h TTL)
  and the intent cache mean re-runs spend far less quota. Do not flush Redis
  between the re-embed and the query steps.
- **Hermetic tests never spend quota** — only `EMBED_MODE=real` and this live
  wave call Gemini.

## 3. Prerequisites

- **Services up.** Postgres (pgvector), Redis, the API, and a Celery **worker**
  must be running (the sample queries go through the real HTTP API + Celery, and
  the compose-worker re-embed alternative needs the worker):

  ```bash
  POSTGRES_HOST_PORT=5442 REDIS_HOST_PORT=6399 docker compose up -d postgres redis api worker beat
  uv run alembic upgrade head
  ```

- **DB / Redis ports.** Scripts read `backend.config` → `.env`, which in this
  repo points at the **compose host ports `localhost:5442` (Postgres) /
  `localhost:6399` (Redis)**. Override with `DATABASE_URL` / `REDIS_URL` (host
  side) or `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` (for `docker compose up`).
  Ensure your `.env` does **not** force `EMBED_MODE=fake` or `TESTING=1` for the
  live run — the script exports `EMBED_MODE=real`, and process env overrides
  `.env`, but a lingering `TESTING=1` should be unset.
- **Superuser owns the corpus.** `seed_corpus` attaches the corpus to the
  **first superuser**, so the sample-query driver logs in as that account. Set:

  ```bash
  export FIRST_SUPERUSER_EMAIL=admin@example.com   # or your value
  export FIRST_SUPERUSER_PASSWORD=<superuser-password>
  ```

- **`dimensions=1024` must be accepted by the live embedding model.** The corpus
  schema is `vector(1024)` and the embedder requests `dimensions=1024` via the
  OpenAI-compat param (Gemini MRL truncates to any dim ≤ native). **If the live
  endpoint rejects `dimensions=1024`, drop to the MRL-safe `768`** — set
  `EMBED_DIM=768` in config **and** the Wave-0 migration (a localized Wave-0
  change: the `vector(1024)` column becomes `vector(768)`), then re-migrate and
  re-embed. This is the one place a live check can force a schema change.
- **Corpus + query vectors MUST share one space.** `seed_corpus` writes
  deterministic **Fake** vectors so hermetic search works offline; a **real-mode
  sync re-embeds** the corpus with Gemini and replaces those vector rows.
  Skipping the re-embed leaves the corpus in Fake space while queries embed in
  Gemini space → `Precision@5` collapses. **Step 1 below is that re-embed, and
  Step 3's `Precision@5` is what proves the two spaces match.**

## 4. Run it

```bash
# No key set → prints this runbook, asserts nothing, exits 0:
bash scripts/verify_live.sh

# Key set → runs the four checks, PASS/FAIL per step, exit = #failed steps:
export GEMINI_STUDIO_API_KEY=<your-free-key>
export FIRST_SUPERUSER_PASSWORD=<superuser-password>
bash scripts/verify_live.sh
```

---

## 5. The four checks — exact manual commands (mirrored by the script)

These are the commands `scripts/verify_live.sh` runs when the key is present;
run them by hand to reproduce or debug any step.

### Step 1 — re-embed the corpus with Gemini

```bash
export EMBED_MODE=real
# Ensure the superuser + datasources exist (idempotent; seed writes Fake vectors):
EMBED_MODE=real uv run python -m backend.scripts.seed
# Replace the Fake vectors with live Gemini embeddings (in-process, all users):
EMBED_MODE=real uv run python - <<'PY'
import asyncio
from sqlalchemy import select
from backend.db.session import async_session_factory
from backend.db.models import User
from backend.workers.sync import sync_all_async

async def main() -> None:
    async with async_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()
    for user in users:
        print("[reembed]", user.id, await sync_all_async(str(user.id)))

asyncio.run(main())
PY
```

**Compose-worker alternative** (worker running; enqueues + polls over HTTP):

```bash
curl -s -X POST localhost:8000/api/v1/sync/trigger -H "Authorization: Bearer $TOKEN"
curl -s        localhost:8000/api/v1/sync/status   -H "Authorization: Bearer $TOKEN"
```

### Step 2 — end-to-end sample queries (3 single / 2 multi / 3 hard)

Login as the superuser (who owns the corpus), submit each query, poll the task
to a terminal state, and print each answer.

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/login/access-token \
  -d "username=$FIRST_SUPERUSER_EMAIL" -d "password=$FIRST_SUPERUSER_PASSWORD" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# For EACH sample query:
curl -s -X POST localhost:8000/api/v1/query -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"query":"emails from sarah about budget"}'
# → {"task_id": "...", "status": "queued", "conversation_id": "..."}
curl -s localhost:8000/api/v1/tasks/<task_id> -H "Authorization: Bearer $TOKEN"
# → poll until status ∈ {success, awaiting_confirmation, failed}; read result.response
```

The eight queries:

| Category | Query | Expected shape |
|---|---|---|
| single | `calendar next week` | events for the tz-resolved next-week range |
| single | `emails from sarah about budget` | sarah's budget email(s) |
| single | `PDFs last month` | Drive PDFs in the last-month window |
| multi | `cancel Turkish Airlines flight` | write-gated → `awaiting_confirmation` + pending action |
| multi | `prepare for Acme meeting` | multi-service read (mail + calendar + drive) |
| hard | `move the meeting with John` | **clarification** (ambiguous "John") |
| hard | `that email about the proposal` | **context resolution** from prior turn |
| hard | `next Tuesday` | **tz-correct** date range |

A query **passes** when it reaches a non-`failed` terminal state with a
non-empty answer (the write-gated one ends `awaiting_confirmation` with a pending
action — that is a pass, not a failure).

### Step 3 — eval: `Precision@5 > 0.8` AND search `< 500 ms`

```bash
EMBED_MODE=real uv run python -m backend.eval.evaluate
```

`evaluate.py` imports the pipeline in-process over the golden set and, under real
inference, **asserts** `Precision@5 > 0.8` and per-query search latency
`< 500 ms` (non-zero exit on breach). Enable the reranker
(`RERANK_ENABLED=true`) only if `Precision@5` falls short.

### Step 4 — Tier-2 contract suite

```bash
uv run pytest -m llm -q
```

Re-runs the classifier/planner JSON-contract categories against the **live**
endpoint (the `-m llm` suite added by Wave F2; skipped by default in CI via
`-m "not llm"`).

---

## 6. Hard gate vs soft gate

| Check | No key (hermetic) | Key present (live) |
|---|---|---|
| Re-embed corpus with Gemini | documented / skipped | **required** (must succeed) |
| Sample `/query` answers | `stub_llm` / documented | **required** — real answers, non-failed |
| `Precision@5 > 0.8` | **soft** — measured, not asserted | **HARD gate** |
| Search `< 500 ms` | **soft** — measured, not asserted | **HARD gate** |
| `pytest -m llm` | skipped (`-m "not llm"`) | **required** — green |

The script's exit code equals the number of failed steps, so CI can treat a live
run as a hard gate while a keyless run always exits `0`.

## 7. Troubleshooting

- **`FAIL` on Step 2 with "could not obtain a superuser token"** — set
  `FIRST_SUPERUSER_PASSWORD` (and run `uv run python -m backend.scripts.seed`
  once so the superuser exists).
- **`FAIL` on Step 2 with "API not reachable"** — start the API + worker
  (`docker compose up -d api worker`); the driver preflights `GET /health`.
- **`Precision@5` collapses despite a real endpoint** — you skipped Step 1; the
  corpus is still in Fake space. Re-run the re-embed so corpus + query vectors
  share one space.
- **Embedding call rejected on `dimensions`** — the model refused
  `dimensions=1024`; drop to `EMBED_DIM=768` in config **and** the Wave-0
  migration, re-migrate, then re-embed (§3).
- **`429` / quota exhausted** — the client already backs off; if you hit the
  **daily** cap, wait for the reset window and keep the golden set ≤ 15.

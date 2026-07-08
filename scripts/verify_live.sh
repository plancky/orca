#!/usr/bin/env bash
#
# scripts/verify_live.sh — Wave F5 live-inference opt-in verification.
#
# Contract:
#   * GEMINI_STUDIO_API_KEY UNSET -> print the manual runbook, assert NOTHING,
#     exit 0. (Hermetic CI stays green; no quota is spent.)
#   * GEMINI_STUDIO_API_KEY SET   -> export EMBED_MODE=real and run the four
#     real-model checks below, in order, reporting PASS/FAIL per step. The exit
#     code is the number of FAILED steps (0 = all green), so CI can gate on it.
#
# The four checks (real model required — free-tier Gemini via the OpenAI-compat
# layer, keyed only by GEMINI_STUDIO_API_KEY):
#   1. Re-embed the corpus with Gemini so corpus + query vectors share ONE space
#      (seed writes Fake vectors; a real-mode sync replaces them with Gemini).
#   2. End-to-end sample queries — 3 single / 2 multi / 3 hard — via
#      POST /api/v1/query, polling GET /api/v1/tasks/{id}, printing each answer.
#   3. python -m backend.eval.evaluate — asserts Precision@5 > 0.8 and
#      search < 500 ms (the thresholds are HARD gates inside evaluate.py).
#   4. pytest -m llm — the Tier-2 contract suite (added by Wave F2).
#
# The automated commands here MIRROR the runbook printed when no key is set and
# the manual steps in docs/verification_live.md.
#
# SECURITY: the key is NEVER hardcoded — it is read from the environment only.
#
# DB/Redis: scripts read backend.config -> .env, which in this repo points at
# the compose host ports localhost:5442 (Postgres) / localhost:6399 (Redis).
# Override with DATABASE_URL / REDIS_URL (or POSTGRES_HOST_PORT/REDIS_HOST_PORT
# for `docker compose up`).

set -uo pipefail

# --------------------------------------------------------------------------- #
# Config — all env-overridable.
# --------------------------------------------------------------------------- #
API_BASE="${API_BASE:-http://localhost:8000}"
API_V1="${API_V1:-/api/v1}"
SU_EMAIL="${FIRST_SUPERUSER_EMAIL:-admin@example.com}"
SU_PASSWORD="${FIRST_SUPERUSER_PASSWORD:-}"
POLL_TIMEOUT="${POLL_TIMEOUT:-90}"   # seconds to await each query's terminal state
TOKEN=""

SINGLE_QUERIES=(
  "calendar next week"
  "emails from sarah about budget"
  "PDFs last month"
)
MULTI_QUERIES=(
  "cancel Turkish Airlines flight"
  "prepare for Acme meeting"
)
HARD_QUERIES=(
  "move the meeting with John"
  "that email about the proposal"
  "next Tuesday"
)

# --------------------------------------------------------------------------- #
# Manual runbook — printed verbatim when GEMINI_STUDIO_API_KEY is absent.
# Mirrors the automated steps; asserts nothing.
# --------------------------------------------------------------------------- #
print_runbook() {
  cat <<'RUNBOOK'
================================================================================
  LIVE-INFERENCE VERIFICATION — MANUAL RUNBOOK  (Wave F5)

  GEMINI_STUDIO_API_KEY is NOT set, so nothing was executed and nothing was
  asserted. Precision@5 and the < 500 ms latency bound are HARD gates ONLY when
  the key is present; hermetically they are soft (documented) checks.

  Get a free key: https://aistudio.google.com/apikey  (Google AI Studio — an
  API key, NOT OAuth). Then set the env below and re-run this script, or run the
  steps by hand. Full detail: docs/verification_live.md
================================================================================

# 0. Prereqs — free key, real embed mode, superuser password, services up.
export GEMINI_STUDIO_API_KEY=<your-free-key>       # https://aistudio.google.com/apikey
export EMBED_MODE=real
export FIRST_SUPERUSER_PASSWORD=<superuser-password>
POSTGRES_HOST_PORT=5442 REDIS_HOST_PORT=6399 docker compose up -d postgres redis api worker beat
uv run alembic upgrade head

# 1. Re-embed the corpus with Gemini so corpus + query vectors share ONE space.
#    (seed_corpus writes deterministic Fake vectors; a real-mode sync REPLACES
#    them with live Gemini embeddings.)
EMBED_MODE=real uv run python -m backend.scripts.seed
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
#    compose-worker alternative (worker must be running):
#      curl -s -X POST localhost:8000/api/v1/sync/trigger -H "Authorization: Bearer $TOKEN"
#      curl -s localhost:8000/api/v1/sync/status        -H "Authorization: Bearer $TOKEN"

# 2. End-to-end sample queries — login as the superuser (who owns the seeded
#    corpus), POST each query, poll the task to a terminal state, print answers.
TOKEN=$(curl -s -X POST localhost:8000/api/v1/login/access-token \
  -d "username=$FIRST_SUPERUSER_EMAIL" -d "password=$FIRST_SUPERUSER_PASSWORD" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# For EACH of the 3 single / 2 multi / 3 hard queries:
curl -s -X POST localhost:8000/api/v1/query -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"query":"emails from sarah about budget"}'
curl -s localhost:8000/api/v1/tasks/<task_id> -H "Authorization: Bearer $TOKEN"
#   single: "calendar next week" | "emails from sarah about budget" | "PDFs last month"
#   multi:  "cancel Turkish Airlines flight" | "prepare for Acme meeting"
#   hard:   "move the meeting with John" (clarify) | "that email about the proposal"
#           (context) | "next Tuesday" (tz-correct range)

# 3. Eval — Precision@5 > 0.8 AND search < 500 ms (HARD gate when the key is set).
EMBED_MODE=real uv run python -m backend.eval.evaluate

# 4. Tier-2 contract suite (the -m llm tests Wave F2 adds).
uv run pytest -m llm -q
================================================================================
RUNBOOK
}

# --------------------------------------------------------------------------- #
# Live helpers (only reached when the key is present).
# --------------------------------------------------------------------------- #

api_healthy() {
  curl -sf "$API_BASE/health" >/dev/null 2>&1
}

login() {
  [ -n "$SU_PASSWORD" ] || return 1
  TOKEN=$(curl -s -X POST "$API_BASE$API_V1/login/access-token" \
    -d "username=$SU_EMAIL" -d "password=$SU_PASSWORD" \
    | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("access_token", ""))
except Exception:
    print("")')
  [ -n "$TOKEN" ]
}

# Step 1: seed (ensures superuser + datasources exist) then a real-mode sync
# that re-embeds every user's corpus with Gemini and replaces the vector rows.
reembed_corpus() {
  EMBED_MODE=real uv run python -m backend.scripts.seed || return 1
  EMBED_MODE=real uv run python - <<'PY' || return 1
import asyncio
from sqlalchemy import select
from backend.db.session import async_session_factory
from backend.db.models import User
from backend.workers.sync import sync_all_async

async def main() -> None:
    async with async_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()
    for user in users:
        result = await sync_all_async(str(user.id))
        print(f"[reembed] user={user.id} -> {result}")

asyncio.run(main())
PY
}

# Submit one query and poll to a terminal state. Echoes "status|answer".
# Returns 0 when the task ends success/awaiting_confirmation, 1 otherwise.
run_query() {
  local q="$1" body task_id status answer deadline
  body=$(python3 -c 'import json, sys; print(json.dumps({"query": sys.argv[1]}))' "$q")
  task_id=$(curl -s -X POST "$API_BASE$API_V1/query" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$body" | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("task_id", ""))
except Exception:
    print("")')
  if [ -z "$task_id" ]; then
    echo "no-task-id|"
    return 1
  fi
  deadline=$(( $(date +%s) + POLL_TIMEOUT ))
  while :; do
    read -r status answer < <(curl -s "$API_BASE$API_V1/tasks/$task_id" \
      -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
r = (d.get("result") or {}).get("response") or d.get("error") or ""
print(d.get("status") or "unknown", r.replace(chr(10), " ")[:280])')
    case "$status" in
      success|awaiting_confirmation)
        echo "$status|$answer"
        return 0 ;;
      failed)
        echo "$status|$answer"
        return 1 ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "timeout|$answer"
      return 1
    fi
    sleep 2
  done
}

# Step 2: run all 8 sample queries; a query passes when it reaches a non-failed
# terminal state with a non-empty answer. Returns the count of failed queries.
run_query_suite() {
  local fails=0 entry group q out rc
  if ! login; then
    echo "  [FAIL] could not obtain a superuser token (set FIRST_SUPERUSER_PASSWORD)"
    return 1
  fi
  local -a suite=()
  for q in "${SINGLE_QUERIES[@]}"; do suite+=("single|$q"); done
  for q in "${MULTI_QUERIES[@]}"; do suite+=("multi|$q"); done
  for q in "${HARD_QUERIES[@]}"; do suite+=("hard|$q"); done
  for entry in "${suite[@]}"; do
    group="${entry%%|*}"
    q="${entry#*|}"
    out=$(run_query "$q")
    rc=$?
    if [ "$rc" -eq 0 ] && [ -n "${out#*|}" ]; then
      echo "  [ok]   ($group) $q"
      echo "         -> ${out#*|}"
    else
      echo "  [FAIL] ($group) $q -> ${out}"
      fails=$((fails + 1))
    fi
  done
  return "$fails"
}

# --------------------------------------------------------------------------- #
# Orchestration (key present).
# --------------------------------------------------------------------------- #
run_live_checks() {
  export EMBED_MODE=real
  local fails=0

  echo "== Step 1/4: re-embed corpus with Gemini (EMBED_MODE=real) =="
  if reembed_corpus; then
    echo "  [PASS] corpus re-embedded into the live Gemini space"
  else
    echo "  [FAIL] corpus re-embed failed"
    fails=$((fails + 1))
  fi

  echo "== Step 2/4: end-to-end sample queries (3 single / 2 multi / 3 hard) =="
  if ! api_healthy; then
    echo "  [FAIL] API not reachable at $API_BASE (start: docker compose up -d api worker)"
    fails=$((fails + 1))
  elif run_query_suite; then
    echo "  [PASS] every sample query returned a non-failed answer"
  else
    echo "  [FAIL] one or more sample queries failed"
    fails=$((fails + 1))
  fi

  echo "== Step 3/4: eval — Precision@5 > 0.8 AND search < 500 ms =="
  if EMBED_MODE=real uv run python -m backend.eval.evaluate; then
    echo "  [PASS] eval thresholds met"
  else
    echo "  [FAIL] eval thresholds not met (or harness error)"
    fails=$((fails + 1))
  fi

  echo "== Step 4/4: Tier-2 contract suite (pytest -m llm) =="
  if uv run pytest -m llm -q; then
    echo "  [PASS] -m llm suite green"
  else
    echo "  [FAIL] -m llm suite red (or none collected)"
    fails=$((fails + 1))
  fi

  echo
  echo "================ SUMMARY ================"
  if [ "$fails" -eq 0 ]; then
    echo "ALL LIVE CHECKS PASSED"
  else
    echo "$fails STEP(S) FAILED"
  fi
  return "$fails"
}

# --------------------------------------------------------------------------- #
# Entry point — the opt-in gate.
# --------------------------------------------------------------------------- #
if [ -z "${GEMINI_STUDIO_API_KEY:-}" ]; then
  echo "GEMINI_STUDIO_API_KEY is not set — no live checks run, nothing asserted."
  print_runbook
  exit 0
fi

echo "GEMINI_STUDIO_API_KEY detected — running live-inference verification (EMBED_MODE=real)."
run_live_checks
exit $?

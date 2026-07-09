#!/usr/bin/env bash
#
# scripts/dev.sh — one-command launcher + teardown for the local dev stack.
#
# The stack is 6 components in 3 tiers:
#
#   infra : postgres (pgvector)  ·  redis                 — long-lived, managed elsewhere
#   app   : api (uvicorn)  ·  celery worker  ·  celery beat   <- THIS SCRIPT owns these
#   ui    : frontend (Vite :5173)                         — `cd frontend && npm run dev`
#
# The three app processes are the ones you restart constantly, so this script
# runs them as detached background processes (logs -> .dev-logs/<name>.log) each
# in its own process group, and tears them down cleanly. It does NOT own infra
# or the frontend: `up` only PREFLIGHT-checks them (and will `docker compose up
# -d postgres` if Postgres is down) and prints how to start anything else.
#
# Host processes read .env directly (pydantic-settings + celery import
# backend.config), so DB/Redis point at the .env ports (5442 / 6399), NOT the
# compose-internal 5432/6379.
#
# Usage:
#   scripts/dev.sh up        [--no-beat]   # start api+worker+beat  (default command)
#   scripts/dev.sh down      [--all]       # stop them   (alias: cleanup)
#   scripts/dev.sh cleanup   [--all]       #   --all also `docker compose stop postgres`
#   scripts/dev.sh restart   [--no-beat]   # down, then up
#   scripts/dev.sh status                  # show all 6 components
#   scripts/dev.sh logs <api|worker|beat>  # tail -f one component's log

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/.dev-logs"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
COMPONENTS=(api worker beat)

# Ports the host app talks to — parsed from .env (fall back to this repo's
# non-default host ports so a missing .env still probes the right place).
_pg_port="$(grep -E '^DATABASE_URL=' .env 2>/dev/null | sed -E 's|.*:([0-9]+)/[^/]*$|\1|' | grep -Ex '[0-9]+' || true)"
_redis_port="$(grep -E '^REDIS_URL=' .env 2>/dev/null | sed -E 's|.*:([0-9]+)/[0-9]+.*$|\1|' | grep -Ex '[0-9]+' || true)"
PG_PORT="${_pg_port:-5442}"
REDIS_PORT="${_redis_port:-6399}"

# ANSI colours (only on a tty).
if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'; else G=''; R=''; Y=''; B=''; Z=''; fi
ok()   { printf '  %s✓%s %s\n' "$G" "$Z" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$Z" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$Z" "$1"; }
head() { printf '%s%s%s\n' "$B" "$1" "$Z"; }

# The exact `uv run` command for each app component.
component_cmd() {
  case "$1" in
    api)    printf 'uv run uvicorn backend.main:app --reload --port %s' "$API_PORT" ;;
    worker) printf 'uv run celery -A backend.workers.celery_app worker --loglevel=info --concurrency=2' ;;
    beat)   printf 'uv run celery -A backend.workers.celery_app beat --loglevel=info' ;;
  esac
}

# Repo-scoped cmdline fragment used to find/kill a component's processes
# (matches uvicorn's reloader child and celery's prefork children too).
component_pat() {
  case "$1" in
    api)    printf 'uvicorn backend.main:app' ;;
    worker) printf 'celery -A backend.workers.celery_app worker' ;;
    beat)   printf 'celery -A backend.workers.celery_app beat' ;;
  esac
}

# Retry: a down port refuses instantly, but a cold localhost lookup can blow one
# short timeout — a single probe there false-negatives and recreates live infra.
tcp_ok() {
  local i
  for i in 1 2 3; do
    timeout 3 bash -c ": >/dev/tcp/$1/$2" 2>/dev/null && return 0
    sleep 0.3
  done
  return 1
}

# Alive if the recorded process-group is alive, or (fallback) any matching proc.
is_running() {
  local pgf="$LOG_DIR/$1.pgid"
  if [ -f "$pgf" ] && kill -0 "-$(cat "$pgf")" 2>/dev/null; then return 0; fi
  pgrep -f "$(component_pat "$1")" >/dev/null 2>&1
}

# ----------------------------------------------------------------------------- #
# start / stop one component
# ----------------------------------------------------------------------------- #
start_one() {
  local name="$1" cmd log pgf
  cmd="$(component_cmd "$name")"; log="$LOG_DIR/$name.log"; pgf="$LOG_DIR/$name.pgid"
  if is_running "$name"; then warn "$name already running — skip"; return 0; fi
  : > "$log"
  # setsid => new session/group led by this pid; record $$ (== PGID) so `down`
  # can group-kill the whole tree (reloader + prefork children) in one shot.
  nohup setsid bash -c "echo \$\$ > '$pgf'; exec $cmd" >>"$log" 2>&1 &
  sleep 0.3
  ok "started $name (pgid $(cat "$pgf" 2>/dev/null || echo '?')) → ${log#"$ROOT"/}"
}

stop_one() {
  local name="$1"
  local pgf="$LOG_DIR/$name.pgid" pat pgid
  pat="$(component_pat "$name")"
  if ! is_running "$name"; then warn "$name not running"; rm -f "$pgf"; return 0; fi
  # 1) graceful group TERM via the recorded pgid
  if [ -f "$pgf" ]; then
    pgid="$(cat "$pgf")"
    kill -TERM "-$pgid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do kill -0 "-$pgid" 2>/dev/null || break; sleep 1; done
    kill -KILL "-$pgid" 2>/dev/null || true
    rm -f "$pgf"
  fi
  # 2) belt-and-suspenders: reap any straggler matching the exact cmdline
  pkill -TERM -f "$pat" 2>/dev/null || true
  sleep 1
  pkill -KILL -f "$pat" 2>/dev/null || true
  ok "stopped $name"
}

# ----------------------------------------------------------------------------- #
# preflight: infra must be reachable before we start the app tier
# ----------------------------------------------------------------------------- #
preflight() {
  command -v uv >/dev/null 2>&1 || { bad "uv not found — install: https://docs.astral.sh/uv/"; exit 1; }

  if tcp_ok localhost "$PG_PORT"; then
    ok "postgres reachable on :$PG_PORT"
  elif command -v docker >/dev/null 2>&1; then
    warn "postgres down on :$PG_PORT — starting compose service…"
    POSTGRES_HOST_PORT="$PG_PORT" docker compose up -d --no-recreate postgres >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do tcp_ok localhost "$PG_PORT" && break; sleep 1; done
    if tcp_ok localhost "$PG_PORT"; then ok "postgres up on :$PG_PORT"; else bad "postgres still down on :$PG_PORT"; exit 1; fi
  else
    bad "postgres down on :$PG_PORT and docker unavailable — start Postgres first"; exit 1
  fi

  if tcp_ok localhost "$REDIS_PORT"; then
    ok "redis reachable on :$REDIS_PORT"
  else
    warn "redis DOWN on :$REDIS_PORT — worker/beat/enqueue WILL fail. Start it, e.g.:"
    warn "    redis-server --port $REDIS_PORT   (or  REDIS_HOST_PORT=$REDIS_PORT docker compose up -d redis)"
  fi
}

# ----------------------------------------------------------------------------- #
# commands
# ----------------------------------------------------------------------------- #
cmd_up() {
  local beat=1 a
  for a in "$@"; do [ "$a" = "--no-beat" ] && beat=0; done
  mkdir -p "$LOG_DIR"
  head "preflight"
  preflight
  head "starting app tier"
  start_one api
  start_one worker
  if [ "$beat" -eq 1 ]; then start_one beat; else warn "beat skipped (--no-beat)"; fi
  printf '\nwaiting for API on :%s …\n' "$API_PORT"
  for _ in $(seq 1 30); do curl -sf -m 2 "http://localhost:$API_PORT/health" >/dev/null 2>&1 && break; sleep 1; done
  printf '\n'
  cmd_status
  printf '\nlogs: %sscripts/dev.sh logs <api|worker|beat>%s   stop: %sscripts/dev.sh down%s\n' "$B" "$Z" "$B" "$Z"
  printf 'docs http://localhost:%s/docs   ·   SPA http://localhost:%s\n' "$API_PORT" "$FRONTEND_PORT"
}

cmd_down() {
  local all=0 a
  for a in "$@"; do [ "$a" = "--all" ] && all=1; done
  head "stopping app tier"
  stop_one beat     # beat first so it can't re-enqueue during teardown
  stop_one worker
  stop_one api
  if [ "$all" -eq 1 ]; then
    head "stopping infra (--all)"
    if docker compose stop postgres >/dev/null 2>&1; then ok "postgres stopped (data volume persists)"; else warn "compose postgres stop failed"; fi
    warn "redis (:$REDIS_PORT host process) and the Vite frontend are left running — not owned by this script"
  fi
}

cmd_status() {
  head "infra"
  if tcp_ok localhost "$PG_PORT";    then ok "postgres  :$PG_PORT"; else bad "postgres  :$PG_PORT (down)"; fi
  if tcp_ok localhost "$REDIS_PORT"; then ok "redis     :$REDIS_PORT"; else bad "redis     :$REDIS_PORT (down)"; fi
  head "app"
  local c pids
  for c in "${COMPONENTS[@]}"; do
    if is_running "$c"; then
      pids="$(pgrep -f "$(component_pat "$c")" 2>/dev/null | tr '\n' ' ')"
      ok "$(printf '%-7s' "$c")pid ${pids:-?}"
    else
      bad "$(printf '%-7s' "$c")(down)"
    fi
  done
  if curl -sf -m 2 "http://localhost:$API_PORT/health" >/dev/null 2>&1; then ok "api /health OK on :$API_PORT"; else warn "api /health not responding on :$API_PORT"; fi
  head "ui"
  if tcp_ok localhost "$FRONTEND_PORT"; then ok "frontend  :$FRONTEND_PORT"; else warn "frontend  :$FRONTEND_PORT (down) — cd frontend && npm run dev"; fi
}

cmd_logs() {
  local name="${1:-}"
  case "$name" in
    api|worker|beat) ;;
    *) echo "usage: scripts/dev.sh logs <api|worker|beat>"; exit 1 ;;
  esac
  local log="$LOG_DIR/$name.log"
  [ -f "$log" ] || { echo "no log yet at ${log#"$ROOT"/} — start it with: scripts/dev.sh up"; exit 1; }
  exec tail -f "$log"
}

# ----------------------------------------------------------------------------- #
# dispatch
# ----------------------------------------------------------------------------- #
case "${1:-up}" in
  up)             shift 2>/dev/null || true; cmd_up "$@" ;;
  down|cleanup)   shift 2>/dev/null || true; cmd_down "$@" ;;
  restart)        shift 2>/dev/null || true; cmd_down; printf '\n'; cmd_up "$@" ;;
  status)         cmd_status ;;
  logs)           shift 2>/dev/null || true; cmd_logs "$@" ;;
  -h|--help|help) sed -n '2,33p' "$0" ;;
  *)              echo "unknown command: ${1:-}"; echo; sed -n '23,33p' "$0"; exit 1 ;;
esac

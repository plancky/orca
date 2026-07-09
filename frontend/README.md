# Orca — Frontend

A React Router v7 SPA (framework mode, `ssr: false`) for **Orca**, the agentic
Google Workspace orchestrator backend. Users sign in with Google, then chat in
natural language over an async, poll-based task API — the backend classifies
intent, plans a DAG, and fans out to Gmail/Calendar/Drive agents; write-gated
actions (send email, create/delete an event, etc.) render as a confirm/cancel
card before anything executes. The SPA also renders a server-backed
conversation history panel and a Gmail/Calendar/Drive sync status bar.
End-to-end typed from the backend's `openapi.json`; all UI is shadcn/ui on
Tailwind v4. Deploys as static assets to Cloudflare Workers.

## Stack

- **React Router v7** framework mode, `ssr: false` (true SPA; one prerendered
  `index.html` shell + client routing)
- **Vite 7** + **TypeScript strict** (`noUncheckedIndexedAccess`,
  `verbatimModuleSyntax`)
- **TanStack Query v5** via **openapi-typescript → openapi-fetch →
  openapi-react-query** (types generated from `openapi.json`)
- **shadcn/ui** (new-york) on **Tailwind CSS v4** (`@tailwindcss/vite`)
- **Cloudflare Workers** static assets (`wrangler`)

## Prerequisites

- **Node 20+ and npm** — this package.
- **Docker + Docker Compose** and **[uv](https://docs.astral.sh/uv/)** — to run
  the backend (Postgres/pgvector + Redis + API + worker + beat). See
  [Development](#development) below, or the [repo root README](../README.md)
  for the full stack and a no-Docker path.
- For local dev the SPA expects the API at `http://localhost:8000` (see `.env`).

## Development

Two terminals: Docker Compose owns the backend (Postgres/Redis/API/worker/
beat), Vite owns the frontend.

### 1 · Backend (from the repo root)

```bash
cp .env.example .env   # first time only
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))" >> .env

docker compose up -d          # postgres, redis, api, worker, beat
uv sync
uv run alembic upgrade head
uv run python backend/scripts/seed.py
```

- API: `http://localhost:8000` — interactive docs at `/docs` (Swagger) and
  `/redoc`. Health check: `curl localhost:8000/health` → `{"status":"ok"}`.
- The `.env.example` defaults (`PROVIDER=mock`, `EMBED_MODE=fake`) run fully
  offline against the seeded mock corpus — no Google/Gemini/Modal credentials
  needed to exercise chat end-to-end.
- Full env var reference and the no-Docker (bare `uvicorn`/`celery`) path are
  in the [root README](../README.md#environment-variables).

> **No source bind-mount or hot-reload in Compose.** `docker-compose.yml`
> builds one `workspace-orchestrator:local` image from `Dockerfile` and runs
> it via `command:` — your working tree isn't mounted in, and the `api`
> container's `uvicorn … --workers 2` doesn't pass `--reload` either way. A
> backend code change needs `docker compose up -d --build api` (or
> `--build worker` / `--build beat`) to land. For fast backend iteration,
> `docker compose stop api` and run
> `uv run uvicorn backend.main:app --reload --port 8000` on the host instead —
> Postgres/Redis are already published on `localhost:5432`/`localhost:6379`,
> so `.env`'s defaults just work.

### 2 · Frontend

```bash
cd frontend
npm install
npm run dev            # Vite dev server + HMR at http://localhost:5173
```

Open **http://localhost:5173**. The login screen is Google-OAuth-only — see
[Authentication](#authentication) below for the local-dev workaround if you
haven't configured a Google OAuth client.

### 3 · Watching logs

| Component | Command |
|---|---|
| API | `docker compose logs -f api` |
| Celery worker (query pipeline) | `docker compose logs -f worker` |
| Celery beat (15-min sync schedule) | `docker compose logs -f beat` |
| Postgres / Redis | `docker compose logs -f postgres redis` |
| Everything | `docker compose logs -f` |
| Frontend | the `npm run dev` terminal (Vite build/HMR errors) **and** the browser console/Network tab (React Query errors, failed fetches, poll traffic) |

Add `--tail=200` to see scrollback beyond the default, or drop `-f` for a
one-shot dump. Every backend service logs to stdout with plain `logging` (no
structured log file, no log aggregator) — `docker compose logs` is the only
place to see them. `docker compose ps` shows per-service health (`api` and
`worker` have real healthchecks; `beat` intentionally disables its
healthcheck — see `docker-compose.yml`).

## Commands

```bash
npm install
npm run dev          # Vite dev server + HMR at http://localhost:5173
npm run typecheck    # react-router typegen && tsc --noEmit  (the contract gate)
npm run gen:api      # regenerate app/lib/api/schema.d.ts from ./openapi.json
npm run build        # → build/client/  (index.html + hashed assets)
npm run preview      # build + wrangler dev (Workers runtime locally)
npm run deploy       # build + wrangler deploy  (needs Cloudflare credentials)
npm run ui:add       # shadcn CLI — add/update a component under components/ui
```

## Environment

The API origin is baked in at build time (a static SPA has no server runtime):

- `.env` → `VITE_API_BASE_URL=http://localhost:8000` (local dev default)
- `.env.production` → set to the deployed backend origin before a prod build

The JWT is obtained at runtime via login and never embedded. The OpenAPI paths
already include `/api/v1`, so `VITE_API_BASE_URL` is the **origin only**.

`VITE_API_BASE_URL` isn't the only place the API origin is wired up:
`vite.config.ts` also proxies `/api/v1/*` to `http://localhost:8000` in dev,
solely so the browser-redirect leg of Google OAuth (registered at the **SPA**
origin — see [Authentication](#authentication)) reaches FastAPI. Regular
`$api` calls use the absolute `VITE_API_BASE_URL` and never touch this proxy.
The deployed Workers build has no equivalent proxy (`wrangler.jsonc` serves
static assets only) — in production, `GOOGLE_REDIRECT_URI` must point at the
deployed **backend** origin directly, not the SPA origin (see
[Deploying to Cloudflare Workers](#deploying-to-cloudflare-workers)).

## Authentication

Sign-in is **Google OAuth only** in the shipped UI: `routes/login.tsx` renders
a single "Sign in with Google" button — no password form is wired up, even
though the backend still exposes `POST /login/access-token` /
`POST /login/test-token` and `lib/auth/useAuth.ts` still has `login`/`logout`
helpers for them (currently unused by any route).

Flow:

1. `login.tsx` sends the browser to `${VITE_API_BASE_URL}/api/v1/auth/google`.
2. FastAPI (`backend/api/routes_auth.py`) redirects to Google's consent
   screen. Google redirects back to `GOOGLE_REDIRECT_URI`
   (`http://localhost:5173/api/v1/auth/callback` in dev — the **SPA** origin,
   not the API origin).
3. In dev, Vite's proxy (see [Environment](#environment)) forwards that
   `/api/v1/*` request to `localhost:8000`, where FastAPI exchanges the code,
   stores Fernet-encrypted Google tokens, enqueues an initial sync, mints a
   JWT, and 302s to `${FRONTEND_URL}/auth/callback#token=<jwt>&email=<email>`.
4. `routes/auth.callback.tsx` reads `token` out of the URL **fragment**
   (never sent to any server), calls `setToken()`, and navigates to `/app`.
   An `?error=` value (consent denied, invalid `state`, OAuth not configured)
   toasts and bounces back to `/login`.
5. The JWT is a bearer token: `localStorage["wso.token"]` mirrors an
   in-memory variable (`lib/auth/token.ts`); every `$api` call attaches
   `Authorization: Bearer <token>`. `<RequireAuth>` (`lib/auth/guard.tsx`)
   validates it on mount via `POST /login/test-token`. There is **no refresh
   endpoint** — an 8-day expiry means re-login — and **no logout control in
   the UI** today.

**Local-dev gotcha:** the Google button 503s ("Google OAuth is not
configured") unless the backend has `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
/ `TOKEN_ENCRYPTION_KEY` set (see the root `.env.example`) with an OAuth
client whose authorized redirect URI is *exactly*
`http://localhost:5173/api/v1/auth/callback`. Without a real Google Cloud
OAuth client, the SPA currently has **no way to log in through the UI**. To
work on the chat UI without setting one up, mint a token with the
still-live password endpoints and inject it by hand:

```bash
curl -s -XPOST localhost:8000/api/v1/users/signup -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"hunter2hunter2","full_name":"Me"}'

TOKEN=$(curl -s -XPOST localhost:8000/api/v1/login/access-token \
  -d 'username=me@example.com&password=hunter2hunter2' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
echo "$TOKEN"
```

Then, in the browser devtools console on `http://localhost:5173`:

```js
localStorage.setItem("wso.token", "<paste TOKEN here>");
location.reload();
```

## Type-safety pipeline

`openapi.json` (copied from the repo root) is the single source of truth.
`npm run gen:api` writes `app/lib/api/schema.d.ts` (committed). Every `$api`
call site is checked against it by `npm run typecheck` — a wrong path, body, or
response access fails `tsc`. The few backend responses that are untyped in the
contract (`TaskPublic.result`/`progress` are `dict[str, Any]`; `/sync/status` is
`list[dict]`) are narrowed by hand in `app/lib/api/domain.ts`.

## Regenerating types when the backend changes

```bash
# from the repo root, after changing the API:
TESTING=1 SECRET_KEY=test-secret-key-32-bytes-minimum-len \
  uv run python backend/scripts/export_openapi.py     # writes ./openapi.json
cp openapi.json frontend/openapi.json
cd frontend && npm run gen:api && npm run typecheck
```

## Deploying to Cloudflare Workers

The default path ships **no Worker script** — the SPA is pure static hitting a
separate API origin. `wrangler.jsonc` serves `build/client/` with
`not_found_handling: "single-page-application"`, so deep links like
`/app/c/<uuid>` hydrate instead of 404ing.

```bash
npm run preview      # verify locally (Workers runtime) — no CF account needed
npm run deploy       # authenticated `wrangler deploy` → *.workers.dev
```

Because the SPA origin differs from the API origin, the FastAPI backend must
send CORS headers for the SPA origin plus the `Authorization` header (it
currently allows all origins). The SPA uses bearer tokens (no cookies), so it
never sets `credentials: "include"`.

Google OAuth needs its own step: `GOOGLE_REDIRECT_URI` and the OAuth client's
"Authorized redirect URI" must both point at the **deployed backend's**
`/api/v1/auth/callback` (there is no Vite dev proxy in production to bounce a
Worker-origin hit over to the API), and `FRONTEND_URL` must be the deployed
`*.workers.dev` origin so the post-login redirect lands back on the real SPA.

## WebSocket progress (backend supports it; frontend is poll-only in v1)

`backend/api/ws.py`'s `WS /ws/query?token=<jwt>` is **fully implemented, not a
stub** — it accepts either an existing `task_id` or a fresh `query` to
enqueue, then streams `node_started` / `node_finished` / `partial` / `done`
frames off the Redis progress stream for sub-second updates, falling back to
DB polling if the stream expires. The frontend simply doesn't open it yet: v1
is **poll-only** (`useTaskPolling`, 1s `GET /tasks/{id}` until terminal) — the
correctness floor that renders every state on its own. To adopt the
enhancement, open the socket when a task is running and stream frames into
`ProgressTrace`; keep the poll running as the source of the terminal `result`,
and degrade to poll-only if the socket fails. `wsUrl()` in `app/lib/config.ts`
already derives the endpoint and is currently unused.

## Layout

```
app/
  root.tsx                    # <html> shell + QueryClient/Tooltip providers + ErrorBoundary
  routes.ts                   # flat route table
  entry.client.tsx            # SPA hydration
  styles/app.css              # Tailwind v4 entrypoint + design tokens
  routes/
    _index.tsx                 # redirect "/" → "/app" (or "/login" if unauthed)
    login.tsx                  # public "Sign in with Google" screen
    auth.callback.tsx          # OAuth redirect target — reads #token, stores it, → /app
    app.tsx                    # authed layout: RequireAuth + StatusBar + ConversationList + <Outlet/>
    app._index.tsx              # empty-state / new-conversation view
    app.c.$conversationId.tsx   # message thread for one conversation
  lib/
    api/      schema.d.ts (generated) · client.ts · query.ts · domain.ts
    auth/     token.ts · useAuth.ts (password-login helpers, unused by any route) · guard.tsx
    chat/     useSendQuery · useTaskPolling · useConfirmAction · useConversation · types
    history/  useConversations
    sync/     useSyncStatus
    config.ts API_BASE_URL · wsUrl()
    utils.ts
  components/
    chat/     MessageThread · MessageInput · ProgressTrace · PendingActionCard
    history/  ConversationList
    status/   StatusBar · ServicePill
    ui/       shadcn primitives (owned/committed)
```

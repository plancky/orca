# Orca — Frontend

A React Router v7 SPA (framework mode, `ssr: false`) for the Agentic Google
Orca backend. Text-only chat over an async, poll-based task
API, with a server-backed conversation history panel and a Gmail/Calendar/Drive
sync status bar. End-to-end typed from the backend's `openapi.json`; all UI is
shadcn/ui on Tailwind v4. Deploys as static assets to Cloudflare Workers.

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

- Node 20+ and npm
- A running backend (see the repo root README). For local dev the SPA expects
  the API at `http://localhost:8000` (see `.env`).

## Commands

```bash
npm install
npm run dev          # Vite dev server + HMR at http://localhost:5173
npm run typecheck    # react-router typegen && tsc --noEmit  (the contract gate)
npm run gen:api      # regenerate app/lib/api/schema.d.ts from ./openapi.json
npm run build        # → build/client/  (index.html + hashed assets)
npm run preview      # build + wrangler dev (Workers runtime locally)
npm run deploy       # build + wrangler deploy  (needs Cloudflare credentials)
```

## Environment

The API origin is baked in at build time (a static SPA has no server runtime):

- `.env` → `VITE_API_BASE_URL=http://localhost:8000` (local dev default)
- `.env.production` → set to the deployed backend origin before a prod build

The JWT is obtained at runtime via login and never embedded. The OpenAPI paths
already include `/api/v1`, so `VITE_API_BASE_URL` is the **origin only**.

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

## WebSocket progress (follow-up, not in v1)

The backend exposes `WS /ws/query?token=<jwt>` streaming `node_started` /
`node_finished` / `partial` / `done` frames for sub-second progress. v1 is
**poll-only** (1s `GET /tasks/{id}` until terminal) — the correctness floor that
renders every state on its own. To add the enhancement later, open the socket
when a task is running and stream frames into `ProgressTrace`; keep the poll
running as the source of the terminal `result`, and degrade to poll-only if the
socket fails. `wsUrl()` in `app/lib/config.ts` derives the endpoint.

## Layout

```
app/
  root.tsx                     # <html> shell + QueryClient/Tooltip providers + ErrorBoundary
  routes.ts                    # flat route table
  entry.client.tsx             # SPA hydration
  routes/                      # _index (redirect) · login · app (layout) · app._index · app.c.$conversationId
  lib/
    api/    schema.d.ts (generated) · client.ts · query.ts · domain.ts
    auth/   token.ts · useAuth.ts · guard.tsx
    chat/   useSendQuery · useTaskPolling · useConfirmAction · useConversation · types
    history/ useConversations · sync/useSyncStatus · config.ts · utils.ts
  components/
    chat/    MessageThread · MessageInput · ProgressTrace · PendingActionCard
    history/ ConversationList     status/ StatusBar · ServicePill
    ui/      shadcn primitives (owned/committed)
```

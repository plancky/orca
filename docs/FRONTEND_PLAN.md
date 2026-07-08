# Plan: Frontend — Agentic Google Workspace Orchestrator

## Context

The backend (`docs/PLAN.md`) is an **async, poll-based** FastAPI orchestrator. Every query enqueues a
Celery task and returns a `task_id`; the client polls `GET /tasks/{id}` (or attaches a WebSocket) until the
task reaches a terminal status. Auth is **stateless JWT** issued from a **form-encoded** OAuth2 password
login. This plan specifies a **React Router v7 SPA** (framework mode, `ssr: false`) that consumes that
contract with **end-to-end type-safety from the backend's OpenAPI spec**, and deploys as **static assets to
Cloudflare Workers**.

Scope requested by the user:
1. Public password-login screen.
2. Dashboard: chat window, **text input only** (no file upload, no voice).
3. Left panel: history of conversations.
4. Each conversation → its messages (user turns + assistant responses).
5. Status bar: which workspace services are connected (Gmail / GCal / GDrive).
6. Frontend types **generated from the backend's OpenAPI** (`openapi.json`).
7. Directory nested under `frontend/`, built + deployed to Cloudflare Workers (static React Router SPA).

### Locked decisions

| Decision | Choice | Consequence |
|---|---|---|
| Framework | **React Router v7, framework mode, `ssr: false`** | True SPA; pre-renders one `index.html` shell at build; client-side routing for everything else |
| Build tool | **Vite** (via `@react-router/dev/vite`) | Single plugin drives the whole build; no separate Vite config gymnastics |
| Language | **TypeScript strict** | `strict: true`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax` |
| Server state | **React Query (TanStack Query v5)** | Owns all server cache: auth-gated queries, polling, mutations, invalidation |
| Type-safety pipe | **`openapi-typescript` → `openapi-fetch` → `openapi-react-query`** | Types + fetch client + RQ hooks all derive from `openapi.json`; zero hand-written DTOs, zero runtime cost |
| Auth transport | **Bearer JWT in `Authorization` header**, token in memory + `localStorage` mirror | Backend is stateless HS256; no cookies, no refresh endpoint exists (8-day expiry, re-login on expiry) |
| Query lifecycle | **Poll `GET /tasks/{id}`** as the baseline; **WebSocket `/ws/query` as a progressive enhancement** | Poll is the correctness floor; WS only upgrades progress latency |
| Routing model | **File-based routes** via `@react-router/dev` `routes.ts` | Public `/login` vs authed `/` split by a layout guard |
| Deploy target | **Cloudflare Workers static assets** (`not_found_handling: "single-page-application"`) | No Worker script in the default path; SPA is pure static, API lives on a separate origin |
| API origin | **`VITE_API_BASE_URL` injected at build time** | SPA and API are different origins; base URL is baked per-environment build |
| Styling | **shadcn/ui on Tailwind CSS v4** (`@tailwindcss/vite`) | **All UI is composed from shadcn components only** — one uniform system, Radix-accessible, themable via CSS variables; no hand-rolled/bespoke component styling |

> **Deviation note vs `docs/PLAN.md`:** the backend plan does **not** expose a conversation-list endpoint —
> `conversations` is the persistence SoR but no `GET /conversations` route is defined (see
> **§8 Backend gaps**). This plan treats history as a **contract addition the backend must ship**, and
> specifies the exact endpoints it needs. Until they exist, the left panel falls back to a **client-side
> session cache** (documented below) so the UI is never blocked on backend work.

---

## Backend contract the frontend consumes

Everything below is lifted verbatim from `docs/PLAN.md` §"API endpoints". Base path: **`/api/v1`**.

### Auth (public → token)
- `POST /api/v1/login/access-token` — **`application/x-www-form-urlencoded`**, body `{username, password}`
  (`username` = the user's email). → `Token{access_token, token_type:"bearer"}`. `400` on bad creds /
  inactive user. **This is the only screen the user asked for.**
- `POST /api/v1/login/test-token` — `Authorization: Bearer <token>` → `UserPublic`. Used as the **token
  health check** on app boot (validates a persisted token before trusting it).
- `GET /api/v1/users/me` — `Authorization: Bearer <token>` → `UserPublic`. Profile for the header/avatar.
- `POST /api/v1/users/signup` — public, `{email, password, full_name}` → `UserPublic`. **Not in the
  requested UI**; wired in the API layer but no route/screen built (noted as optional in §7).

### Chat (the core async loop)
- `POST /api/v1/query` — JSON `{query, conversation_id, confirm?:{action_id, decision}}` → **HTTP 202**
  `{task_id, status:"queued", conversation_id}`. Enqueues an orchestrate task, or a confirm/resume task
  when `confirm` is present.
- `GET /api/v1/tasks/{task_id}` → `{task_id, kind, status, progress, result?, error?, pending_actions?,
  parent_task_id?, conversation_id, updated_at}`. **Poll target.** Terminal statuses:
  `success | failed | awaiting_confirmation`. Non-terminal: `queued | running`.
- `WS /ws/query` — accepts `{task_id}` (attach) or `{query, conversation_id}` (enqueue+attach). Emits
  `node_started | node_finished | partial | suspended` frames, then a final `done` frame carrying
  `tasks.result`. **Progressive enhancement over the poll.**

### Status bar
- `GET /api/v1/sync/status` → per-service `last_synced_at` + `item_count` for Gmail / GCal / GDrive.
  Drives the "which services are connected" indicator.
- `POST /api/v1/sync/trigger` → enqueue a one-shot sync for the calling user. Wired to a manual "refresh"
  affordance in the status bar.

### Task status model (drives chat rendering)

```
queued ──▶ running ──▶ success            (assistant bubble = result.response)
                   └──▶ awaiting_confirmation   (render pending_actions → Approve / Deny)
                   └──▶ failed                   (error bubble; offer retry)

awaiting_confirmation ──(POST /query {confirm})──▶ NEW task_id (parent_task_id set) ──▶ poll again
```

The frontend's chat engine is a **state machine over this vocabulary**, not a request/response call.

---

## Directory structure (nested under `frontend/`)

```
frontend/
  app/
    root.tsx                     # <html> shell, providers (QueryClientProvider), <Outlet/>, error boundary
    routes.ts                    # route table (@react-router/dev flat config)
    entry.client.tsx             # hydrateRoot (SPA entry)
    routes/
      _index.tsx                 # redirect "/" → "/app" (or "/login" if unauthed)
      login.tsx                  # PUBLIC password-login screen
      app.tsx                    # AUTHED layout: guard + status bar + <Outlet/> (left panel lives here)
      app._index.tsx             # empty-state / "start a new conversation" pane
      app.c.$conversationId.tsx  # a single conversation: message thread + chat input
    lib/
      api/
        schema.d.ts              # GENERATED by openapi-typescript from ../openapi.json — DO NOT EDIT
        client.ts                # openapi-fetch createClient + bearer auth middleware
        query.ts                 # openapi-react-query $api = createClient(fetchClient)
      auth/
        token.ts                 # get/set/clear token (localStorage mirror + in-memory)
        useAuth.ts               # login mutation, logout, bootstrap-from-token, isAuthenticated
        guard.tsx                # <RequireAuth> — redirects to /login when no valid token
      chat/
        useSendQuery.ts          # POST /query → returns task_id
        useTaskPolling.ts        # GET /tasks/{id} polled via RQ refetchInterval until terminal
        useConfirmAction.ts      # POST /query {confirm} → resume task
        useTaskSocket.ts         # OPTIONAL: WS /ws/query progressive enhancement
        conversationStore.ts     # client-side history fallback (see §8) — Map<conversationId, Turn[]>
      sync/
        useSyncStatus.ts         # GET /sync/status (polled) + POST /sync/trigger mutation
      config.ts                  # reads import.meta.env.VITE_API_BASE_URL (validated at load)
    components/
      chat/                      # feature components — COMPOSED FROM ui/ (shadcn) only, no bespoke CSS
        MessageThread.tsx        # user/assistant/progress/pending bubbles — Card, Avatar, ScrollArea
        MessageInput.tsx         # text-only Textarea + Button (send); Enter=send, disabled while in-flight
        ProgressTrace.tsx        # node_started/finished timeline — Badge + Skeleton (running)
        PendingActionCard.tsx    # awaiting_confirmation — Card + Button (Approve/Deny) + AlertDialog
      history/
        ConversationList.tsx     # LEFT PANEL — Button ("New chat") + ScrollArea + list rows (active state)
      status/
        StatusBar.tsx            # top bar — Gmail/GCal/GDrive pills + refresh Button
        ServicePill.tsx          # one service — Badge (connected/stale/disconnected) + Tooltip (item_count)
      ui/                        # shadcn/ui primitives — GENERATED by `npx shadcn add`, owned/committed
                                 #   button card input textarea avatar badge scroll-area skeleton
                                 #   tooltip alert-dialog sonner (toasts) dropdown-menu
    lib/
      utils.ts                   # shadcn `cn()` (clsx + tailwind-merge) — required by every ui/ component
    styles/
      app.css                    # Tailwind v4 entry: @import "tailwindcss" + shadcn CSS-variable theme
  public/                        # static passthrough (favicon, etc.)
  openapi.json                   # COPIED from repo-root openapi.json (backend's export_openapi.py output)
  components.json                # shadcn/ui config: style, aliases (~/*), tailwind css path, lucide icons
  react-router.config.ts         # ssr: false
  vite.config.ts                 # reactRouter() + tailwindcss() plugins
  wrangler.jsonc                 # Cloudflare Workers static-assets config
  tsconfig.json                  # strict; paths alias "~/*" → "app/*"
  package.json
  .env                           # VITE_API_BASE_URL (local dev default)
  .env.production                # VITE_API_BASE_URL (prod backend origin)
  .dev.vars                      # (only if the optional Worker-proxy variant is used)
```

**Why this shape.** Routes are thin; all server-state logic lives in `lib/*` hooks so components stay
declarative. `lib/api/schema.d.ts` is the single generated source of truth; nothing else declares request
or response types. The `frontend/` root is self-contained and independently buildable/deployable.

---

## Type-safety pipeline (OpenAPI → types → hooks)

The contract is generated, never hand-written. Three layers, all keyed off the backend's `openapi.json`.

**1. Generate types** (`openapi-typescript`) — run whenever the backend contract changes:

```jsonc
// package.json → scripts
"gen:api": "openapi-typescript ./openapi.json -o ./app/lib/api/schema.d.ts"
```

The backend already emits `openapi.json` at repo root via `scripts/export_openapi.py` (see `docs/PLAN.md`
§"Deliverables"). CI copies it into `frontend/openapi.json` before `gen:api`, so the generated types track
the real server. **`schema.d.ts` is committed** so builds are reproducible without a live backend.

**2. Typed fetch client** (`openapi-fetch`) with a bearer-auth middleware — `app/lib/api/client.ts`:

```ts
import createFetchClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { getToken, clearToken } from "~/lib/auth/token";
import { API_BASE_URL } from "~/lib/config";

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) clearToken(); // token expired/tampered → force re-login
    return response;
  },
};

export const fetchClient = createFetchClient<paths>({ baseUrl: API_BASE_URL });
fetchClient.use(authMiddleware);
```

**3. React Query hooks** (`openapi-react-query`) — `app/lib/api/query.ts`:

```ts
import createClient from "openapi-react-query";
import { fetchClient } from "./client";

export const $api = createClient(fetchClient);
// usage: $api.useQuery("get", "/api/v1/tasks/{task_id}", { params: { path: { task_id } } })
//        $api.useMutation("post", "/api/v1/query")
```

Every call site is now **compile-time checked against the server contract**: wrong path, wrong body shape,
or wrong response access fails `tsc`. `npm run typecheck` (`tsc --noEmit`) runs in CI as the contract gate.

> **Form-encoded login caveat.** `POST /login/access-token` is `x-www-form-urlencoded`, which `openapi-fetch`
> supports via `bodySerializer` + `URLSearchParams`. The login mutation sets
> `bodySerializer: (b) => new URLSearchParams(b as Record<string,string>)` and the form
> `Content-Type`. This is the one call that opts out of JSON serialization — documented in `useAuth.ts`.

---

## UI system — shadcn/ui (the only component source)

**Hard rule: every visible element is a shadcn/ui component or a composition of them.** No bespoke
component styling, no ad-hoc `<div className="...">` widgets that reinvent a button/card/input. This keeps
the whole app on one uniform, accessible (Radix-backed), themable system.

**Setup (Vite + React Router v7 + Tailwind v4), one-time:**

1. Tailwind v4 is already wired (`@tailwindcss/vite` in `vite.config.ts`); `app/styles/app.css` begins with
   `@import "tailwindcss";` followed by the shadcn CSS-variable theme block the CLI writes.
2. Path alias: shadcn needs the `~/*` → `app/*` alias present in **both** `tsconfig.json` (`compilerOptions.paths`)
   and `vite.config.ts` (`resolve.alias`), so generated imports like `~/lib/utils` resolve.
3. `components.json` at `frontend/` root (adapted to this repo's alias + `app/` layout):

```jsonc
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "app/styles/app.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "~/components",
    "utils": "~/lib/utils",
    "ui": "~/components/ui",
    "lib": "~/lib",
    "hooks": "~/lib"
  },
  "iconLibrary": "lucide"
}
```

4. `app/lib/utils.ts` exports `cn()` (`clsx` + `tailwind-merge`) — every generated `ui/` component imports it.

**Adding components (CLI, checked into the repo):**
```bash
npx shadcn@latest add button card input textarea avatar badge \
  scroll-area skeleton tooltip alert-dialog sonner dropdown-menu
```
Generated files land in `app/components/ui/` and are **owned, edited, and committed** (shadcn is copy-in,
not a dependency). Feature components in `components/{chat,history,status}/` import exclusively from
`~/components/ui/*` — that import boundary is the enforcement point for "shadcn only".

**Component → primitive mapping** (what each feature UI is built from):

| Feature component | shadcn primitives |
|---|---|
| `MessageThread` / message bubbles | `Card`, `Avatar`, `ScrollArea` |
| `MessageInput` (text-only) | `Textarea`, `Button` |
| `ProgressTrace` (running task) | `Badge`, `Skeleton` |
| `PendingActionCard` (write gate) | `Card`, `Button`, `AlertDialog` |
| `ConversationList` (left panel) | `Button`, `ScrollArea` |
| `StatusBar` / `ServicePill` | `Badge`, `Tooltip`, `Button` |
| login form | `Card`, `Input`, `Button` |
| toasts (errors, "sync triggered") | `sonner` (`Toaster` + `toast()`) |

**Theming**: a single CSS-variable theme in `app.css` (light/dark via `.dark` class) drives all colors;
no per-component color overrides. Icons come from `lucide-react` (shadcn's default), not mixed icon sets.

---

## Feature design

### 1. Public login screen (`routes/login.tsx`)
- Email + password fields, submit → `useAuth().login`.
- `login` = `$api.useMutation("post", "/api/v1/login/access-token")` with the form-encoded serializer;
  on success stores `access_token` via `token.ts`, invalidates `users/me`, navigates to `/app`.
- `400` → inline "Invalid email or password". Network error → retry affordance.
- If a valid token already exists (verified via `login/test-token` on mount), redirect straight to `/app`.

### 2. Auth guard + bootstrap (`lib/auth/`)
- **Token storage**: in-memory variable (authoritative for the tab) mirrored to `localStorage` so refreshes
  survive. `clearToken()` on `401` or explicit logout.
- **Bootstrap**: on app load, if a token exists, fire `POST /login/test-token`; success → authed, failure →
  clear + route to `/login`. No refresh-token flow exists in the backend, so **expiry = re-login** (8-day
  window per `docs/PLAN.md`).
- `<RequireAuth>` wraps the `/app` layout; unauthenticated access to any authed route bounces to `/login`.

### 3. Dashboard layout (`routes/app.tsx`)
- Three regions: **left** `ConversationList`, **top** `StatusBar`, **main** `<Outlet/>` (thread + input).
- Layout owns the `StatusBar` and history panel so they persist across conversation switches.

### 4. Chat window — **text input only** (`components/chat/`)
The chat engine is a state machine over the task lifecycle:

1. User types in `MessageInput` (a plain `<textarea>`, Enter=send, Shift+Enter=newline). **No file/voice
   affordances** — text only, per spec. Input is **disabled while a task is in-flight** for that conversation.
2. Send → `useSendQuery` = `$api.useMutation("post","/api/v1/query")` with `{query, conversation_id}`.
   Optimistically append the user turn to the thread; capture returned `task_id`.
3. `useTaskPolling(task_id)` = `$api.useQuery("get","/api/v1/tasks/{task_id}")` with
   `refetchInterval: (q) => isTerminal(q.state.data?.status) ? false : 1000`. Poll every ~1s until terminal.
4. Render by status:
   - `queued | running` → assistant "typing" bubble + `ProgressTrace` (from `progress`, or WS frames).
   - `success` → assistant bubble = `result.response`; render `result.actions_taken` as a compact
     ✓-summary.
   - `awaiting_confirmation` → `PendingActionCard` per `pending_actions` entry → Approve/Deny buttons.
   - `failed` → error bubble with `error` + "Retry" (re-sends the same query).
5. **Confirm/resume**: Approve/Deny → `useConfirmAction` posts `{confirm:{action_id, decision}}`; the
   response is a **new** `task_id` (`parent_task_id` set) → poll it the same way. The card is replaced by
   the resumed task's progress. Recursive suspends are handled naturally (each hop is just another task_id).
6. **WebSocket enhancement** (`useTaskSocket`, optional): when a task is running, open `/ws/query` with
   `{task_id}` and stream `node_started/finished/partial` into `ProgressTrace` for sub-second progress; the
   poll **still runs** as the correctness floor and as the source of the terminal `result`. If WS fails to
   connect, the UI degrades silently to poll-only.

### 5. Left panel — conversation history (`components/history/ConversationList.tsx`)
- Lists conversations (id + a title derived from the first user query + timestamp), highlights the active
  one, and has a **"New chat"** button that mints a fresh client-side `conversation_id` (UUID) and routes to
  `/app/c/:conversationId`.
- **Data source depends on backend (§8):** if the backend ships `GET /conversations` + `GET
  /conversations/{id}`, the panel and thread hydrate from the server (React Query, server = SoR). Until
  then, it reads the **client-side `conversationStore`** (a per-session `Map` persisted to `localStorage`)
  populated as the user chats. The component consumes a single `useConversations()` hook so swapping the
  fallback for the real endpoint is a one-file change.

### 6. Status bar — connected services (`components/status/StatusBar.tsx`)
- `useSyncStatus` = `$api.useQuery("get","/api/v1/sync/status")`, polled on a slow interval (~30s).
- One `ServicePill` per service (Gmail / GCal / GDrive): **connected** (recent `last_synced_at`),
  **stale** (synced but old), or **not connected** (absent/never). Shows `item_count`.
- A refresh control fires `POST /sync/trigger` (`useSyncStatus().trigger`) then refetches status.

---

## Cloudflare Workers deployment (static React Router SPA)

Canonical July-2026 setup (Cloudflare Workers **Static Assets**, not the deprecated Pages flow). Our SPA is
**pure static hitting a separate API origin**, so the default path ships **no Worker script**.

**`react-router.config.ts`** — SPA mode:
```ts
import { type Config } from "@react-router/dev/config";
export default { ssr: false } satisfies Config; // build → build/client/ (index.html + hashed assets)
```

**`vite.config.ts`**:
```ts
import { defineConfig } from "vite";
import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [reactRouter(), tailwindcss()],
  build: { sourcemap: false },
});
```

**`wrangler.jsonc`** — static-only SPA (no `main`, no binding; deep-link fallback to `index.html`):
```jsonc
{
  "$schema": "./node_modules/wrangler/config-schema.json",
  "name": "workspace-orchestrator-frontend",
  "compatibility_date": "2026-07-08",
  "assets": {
    "directory": "./build/client",
    "not_found_handling": "single-page-application"
  }
}
```
`not_found_handling: "single-page-application"` serves `/index.html` (200) for any unmatched path, so
deep links like `/app/c/<uuid>` hydrate correctly instead of 404ing.

**`package.json` scripts**:
```jsonc
{
  "dev": "react-router dev",
  "build": "react-router build",
  "preview": "npm run build && wrangler dev",
  "deploy": "npm run build && wrangler deploy",
  "typecheck": "react-router typegen && tsc --noEmit",
  "gen:api": "openapi-typescript ./openapi.json -o ./app/lib/api/schema.d.ts",
  "ui:add": "shadcn add",
  "cf-typegen": "wrangler types"
}
```

**API origin injection (build-time).** A static SPA has no server runtime, so the backend origin is baked
in at build via Vite's `VITE_*` mechanism. `app/lib/config.ts`:
```ts
const url = import.meta.env.VITE_API_BASE_URL;
if (!url) throw new Error("VITE_API_BASE_URL is required at build time");
export const API_BASE_URL = url; // e.g. https://api.example.com
```
- `.env` (local) → `VITE_API_BASE_URL=http://localhost:8000`
- `.env.production` → `VITE_API_BASE_URL=https://<prod-api-origin>`
- **No secrets in `VITE_*`** — the JWT is obtained at runtime via login, never embedded.

**CORS.** SPA origin ≠ API origin, so the **FastAPI backend must send `CORS` headers**
(`CORSMiddleware` allowing the Workers origin + `Authorization` header). This is a **backend config item**
(see §8). *Escape hatch:* if cross-origin CORS is undesirable, add a Worker script with
`assets.binding: "ASSETS"` + `run_worker_first: ["/api/*"]` that proxies `/api/*` to the backend so the SPA
calls same-origin — documented as an alternative, not the default.

**Commands**: `npm run dev` (HMR at :5173) · `npm run preview` (Workers runtime locally) ·
`npm run deploy` (build + `wrangler deploy` → `*.workers.dev` or a custom domain).

---

## Backend gaps this frontend surfaces (action items for the backend)

These are **contract additions the frontend needs**; none exist in `docs/PLAN.md` today. Listed so they can
be scheduled, not silently worked around.

1. **Conversation history endpoints (required for the left panel).** `conversations` is the SoR but no route
   reads it. Needed:
   - `GET /api/v1/conversations` → list `{conversation_id, title|first_query, updated_at}` for the user.
   - `GET /api/v1/conversations/{conversation_id}` → ordered turns `{query, response, created_at, task_id}`.
   Until shipped, the panel uses the **client-side `conversationStore` fallback** (history only for the
   current browser session). One hook (`useConversations`) isolates the swap.
2. **CORS for the Workers origin.** FastAPI must allow the SPA's origin and the `Authorization` header, or
   the SPA can't call the API cross-origin (unless the Worker-proxy escape hatch is used).
3. **`openapi.json` availability to the frontend build.** Backend already exports it (`export_openapi.py`);
   CI must copy repo-root `openapi.json` → `frontend/openapi.json` before `gen:api`.
4. *(Minor)* **WS auth.** `docs/PLAN.md` doesn't state how `/ws/query` authenticates. Browsers can't set
   `Authorization` on a WS handshake, so the backend needs a token-query-param or subprotocol scheme. WS is
   optional here, so this only blocks the progress-enhancement, not the core loop.

---

## Verification

- **Type gate**: `npm run gen:api && npm run typecheck` → clean. Deliberately break a request body → `tsc`
  fails (proves types are wired to the contract).
- **Build**: `npm run build` → `build/client/index.html` + hashed assets present.
- **UI system**: every component under `components/{chat,history,status}/` imports only from
  `~/components/ui/*`; grep for a bespoke styled element outside `ui/` returns nothing. `components.json`
  present, `lib/utils.ts` `cn()` present, shadcn theme variables in `app.css`.
- **Deploy smoke**: `npm run preview` → open `http://localhost:8787`, deep-link `/app/c/<uuid>` returns the
  SPA (not 404), confirming `single-page-application` fallback.
- **Login**: bad creds → inline 400 error; good creds → token stored, redirect to `/app`; reload → stays
  authed via `login/test-token`; tamper the stored token → bounced to `/login` on next `401`.
- **Chat happy path**: send "emails from sarah about budget" → user bubble appears, poll shows
  `queued → running → success`, assistant bubble = `result.response`, input re-enabled.
- **Write-gate path**: a mutating query → `awaiting_confirmation` → `PendingActionCard` renders →
  **Approve** posts confirm, a new `task_id` (with `parent_task_id`) polls to `success` and the write
  summary renders; **Deny** ends with no write.
- **Status bar**: `/sync/status` renders three pills with correct connected/stale state + item counts;
  refresh triggers `/sync/trigger` then updates.
- **History**: left panel lists conversations, switching routes swaps the thread; "New chat" mints a fresh
  `conversation_id`. (Server-backed once §8.1 lands; session-cache fallback until then.)
- **Degradation**: kill the WS → progress still advances via poll and the terminal `result` still renders.

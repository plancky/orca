# API.md — Endpoint Reference

Base URL: `http://localhost:8000` · API prefix: `/api/v1` · Interactive docs:
[`/docs`](http://localhost:8000/docs) (Swagger UI), [`/redoc`](http://localhost:8000/redoc)
(ReDoc). The machine-readable contract is committed at
[`openapi.json`](openapi.json) and served live at `/openapi.json`.

## Conventions

- **Auth.** Protected routes require an `Authorization: Bearer <JWT>` header. Obtain
  a token from [`POST /login/access-token`](#post-apiv1loginaccess-token). The JWT is
  HS256, `sub` = the user's UUID, 8-day expiry. `user_id` is established at the HTTP
  boundary and passed to Celery tasks as a **plain UUID argument** — tokens are never
  stored in Redis or forwarded into task bodies.
- **Async model.** `POST /query` **enqueues** a Celery task and returns `202` with a
  `task_id`. The client then **polls** [`GET /tasks/{task_id}`](#get-apiv1taskstask_id)
  or attaches [`WS /ws/query`](#ws-wsquery). The `tasks` row is the lifecycle
  source-of-truth: `queued → running → (awaiting_confirmation)? → success | failed`.
- **Multi-tenant isolation.** Every read is scoped to the authenticated user;
  requesting another user's `task_id` returns `404` (not `403`, to avoid leaking
  existence).
- **Content type.** JSON for all endpoints **except** `POST /login/access-token`,
  which is `application/x-www-form-urlencoded` (the OAuth2 password flow).

## Common error shape

FastAPI returns errors as `{"detail": "<message>"}` (or a validation array for
`422`). Standard codes: `401` (missing/invalid/expired token, with
`WWW-Authenticate: Bearer`), `400` (inactive user / bad credentials / duplicate
email), `403` (insufficient privileges), `404` (not found / not owned), `422`
(request validation), `429` (rate limit exceeded).

---

## Endpoint index

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/query` | Bearer | Enqueue an orchestration (or confirm/resume) task |
| `GET`  | `/api/v1/tasks/{task_id}` | Bearer | Poll task status / progress / result |
| `POST` | `/api/v1/login/access-token` | — (form) | Log in → JWT |
| `POST` | `/api/v1/login/test-token` | Bearer | Validate a token, echo the user |
| `GET`  | `/api/v1/users/me` | Bearer | Current user's profile |
| `POST` | `/api/v1/users/` | Superuser | Admin-provision a user |
| `POST` | `/api/v1/users/signup` | — | Open self-registration |
| `GET`  | `/api/v1/auth/google` | — | Phase-2 OAuth stub → `501` |
| `POST` | `/api/v1/sync/trigger` | Bearer | Enqueue a corpus sync+embed run |
| `GET`  | `/api/v1/sync/status` | Bearer | Per-service last-sync timestamps |
| `WS`   | `/ws/query` | token query param | Live progress stream |
| `GET`  | `/health` | — | Liveness probe |

---

## `POST /api/v1/query`

Enqueues an orchestration task. If `confirm` is present, resolves the pending
action to its parent task's checkpoint and enqueues a **confirm/resume** task
instead. Returns immediately (`202`).

- **Auth:** `Bearer`
- **Status:** `202 Accepted`
- **Rate limit:** 100 queries/user/hour (`429` on overflow)

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes | The natural-language query (also the user turn's content). |
| `conversation_id` | UUID | no | Omit to auto-create a new conversation (title = `query[:80]`). |
| `confirm` | object | no | `{ "action_id": UUID, "decision": string }` — approve/deny a write-gated action. `decision` is e.g. `"approved"` / `"denied"`. |

```jsonc
// Single-service
{ "query": "Find emails from sarah@company.com about the budget" }

// Follow-up in an existing conversation
{ "query": "Prepare for tomorrow's meeting with Acme Corp",
  "conversation_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301" }

// Confirm a write-gated action (from a prior awaiting_confirmation task)
{ "query": "Yes, send it.",
  "conversation_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
  "confirm": { "action_id": "b1e5…", "decision": "approved" } }
```

**Response** `202` — `QueryResponse`

| Field | Type | Notes |
|---|---|---|
| `task_id` | UUID | Poll this at `GET /tasks/{task_id}`. |
| `status` | string | `"queued"` on enqueue. |
| `conversation_id` | UUID | The (possibly newly created) conversation. |

```json
{ "task_id": "9b2c…", "status": "queued", "conversation_id": "3f2504e0-…" }
```

**Errors:** `401` (auth), `404` (`confirm.action_id` has no resolvable checkpoint),
`422` (missing `query`), `429` (rate limit).

---

## `GET /api/v1/tasks/{task_id}`

Polls the lifecycle row. Scoped to the authenticated user.

- **Auth:** `Bearer` · **Status:** `200` · **Response model:** `TaskPublic`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | The task id (equals the `task_id` returned by `POST /query`). |
| `kind` | string | `"query"` or `"confirm"`. |
| `status` | string | `queued` \| `running` \| `awaiting_confirmation` \| `success` \| `failed`. |
| `conversation_id` | UUID | Owning conversation. |
| `progress` | object \| null | Latest progress snapshot (mirrors the Redis stream), e.g. current node. |
| `result` | object \| null | On `success`: the `TaskResult` (below). |
| `error` | string \| null | On `failed`: the error message. |
| `parent_task_id` | UUID \| null | Set on resume tasks — links back to the suspended parent. |
| `created_at` / `updated_at` | datetime | Timestamps. |

**`result` (TaskResult) shape**

```jsonc
{
  "response": "I found 2 emails from sarah@company.com about the Q3 budget…",
  "actions_taken": [
    { "tool": "gmail.search_emails", "args": { "sender": "sarah@company.com" },
      "result": { "count": 2 }, "status": "executed" }
  ],
  "pending_actions": [        // present (non-null) only when status=awaiting_confirmation
    { "action_id": "b1e5…", "tool": "gmail.send_email",
      "args": { "to": "support@turkishairlines.com" },
      "preview": "Draft: cancellation request for PNR TK4471" }
  ]
}
```

**Example — lifecycle** (successive polls of the same `task_id`):

```jsonc
{ "id": "9b2c…", "kind": "query", "status": "queued",  "progress": null, "result": null }
{ "id": "9b2c…", "kind": "query", "status": "running", "progress": { "node": "gmail.search_emails" } }
{ "id": "9b2c…", "kind": "query", "status": "success", "result": { "response": "…", "actions_taken": [ … ] } }
```

**Errors:** `401` (auth), `404` (unknown id **or** owned by another user).

---

## `POST /api/v1/login/access-token`

OAuth2 password flow — **form-encoded, not JSON**. This is what the Swagger UI
"Authorize" button drives.

- **Auth:** none · **Content-Type:** `application/x-www-form-urlencoded`
- **Status:** `200` · **Response:** `Token`

| Form field | Notes |
|---|---|
| `username` | The user's **email**. |
| `password` | The user's password. |

```json
{ "access_token": "eyJhbGciOiJIUzI1NiІ…", "token_type": "bearer" }
```

**Errors:** `400` (incorrect email/password **or** inactive user).

```bash
curl -XPOST localhost:8000/api/v1/login/access-token \
  -d 'username=me@example.com&password=hunter2hunter2'
```

---

## `POST /api/v1/login/test-token`

Validates the bearer token and echoes the authenticated user — a token health check.

- **Auth:** `Bearer` · **Status:** `200` · **Response:** `UserPublic`
- **Errors:** `401` (invalid/expired/tampered), `404`, `400` (inactive).

---

## `GET /api/v1/users/me`

Returns the authenticated user's own profile.

- **Auth:** `Bearer` · **Status:** `200` · **Response:** `UserPublic`

**`UserPublic` shape**

```json
{ "id": "…", "email": "me@example.com", "full_name": "Me",
  "is_active": true, "is_superuser": false, "timezone": null,
  "created_at": "2026-01-01T00:00:00Z" }
```

**Errors:** `401` (auth), `400` (inactive user).

---

## `POST /api/v1/users/`

Admin-provisioned user creation. Requires a **superuser** token.

- **Auth:** `Bearer` (superuser) · **Status:** `201` · **Response:** `UserPublic`

**Request body** (`UserCreate`): `email`, `password`, optional `full_name`,
`is_active`, `is_superuser`, `timezone`.

**Errors:** `401` (auth), `403` (not a superuser), `400` (email already exists).

---

## `POST /api/v1/users/signup`

Open self-registration. **Public** — no auth.

- **Auth:** none · **Status:** `201` · **Response:** `UserPublic`

**Request body** (`UserRegister`) — **only** these three fields are accepted:

| Field | Type | Required |
|---|---|---|
| `email` | string | yes |
| `password` | string | yes |
| `full_name` | string | no |

> **Privilege-escalation guard.** `is_superuser` / `is_active` are **not** part of
> the signup body and cannot be injected — the route explicitly constructs the user
> with safe defaults, so a body containing `"is_superuser": true` yields a normal
> (non-superuser) user.

**Errors:** `400` (email already exists), `422` (missing email/password).

---

## `GET /api/v1/auth/google`

Phase-2 OAuth placeholder. **Always** raises `501 Not Implemented` — no
`google-auth` import, no OAuth state machine exists in Phase 1.

```json
{ "detail": "Google OAuth not implemented in Phase 1" }
```

---

## `POST /api/v1/sync/trigger`

Enqueues a one-shot run of the 15-min sync+embed beat (fetch new/changed items →
upsert `*_datasource` → chunk + embed inline → write `*_vector_store`).

- **Auth:** `Bearer` · **Status:** `200`

```json
{ "status": "enqueued" }
```

---

## `GET /api/v1/sync/status`

Per-service sync status for the authenticated user.

- **Auth:** `Bearer` · **Status:** `200` · **Response:** array

```json
[
  { "service": "gmail", "last_synced_at": "2026-01-01T12:00:00Z", "item_count": 8 },
  { "service": "gcal",  "last_synced_at": "2026-01-01T12:00:00Z", "item_count": 6 },
  { "service": "gdrive","last_synced_at": "2026-01-01T12:00:00Z", "item_count": 4 }
]
```

---

## `WS /ws/query`

Live progress stream — the push counterpart to polling. Two observers watch one
execution path (the Celery task): the WS subscribes to the Redis stream
`stream:tasks:{task_id}` and forwards each event verbatim.

- **Path:** `/ws/query` (no `/api/v1` prefix)
- **Auth:** JWT via **query parameter** — `ws://localhost:8000/ws/query?token=<JWT>`
  (browsers can't set `Authorization` on a WS handshake). A missing/invalid token
  closes the socket with code `1008`.

**First client message** (JSON) — one of:

```jsonc
{ "task_id": "9b2c…" }                                   // attach to an existing task
{ "query": "…", "conversation_id": "3f25…" }            // enqueue then attach
```

When enqueuing via `{query}`, the server replies first with
`{ "type": "task_created", "task_id": "…" }`, then streams progress frames.

**Server frames** — `ProgressEvent`, one of:

| `type` | Fields | Meaning |
|---|---|---|
| `task_created` | `task_id` | (enqueue mode only) the freshly issued id |
| `node_started` | `task_id`, `node_id`, `timestamp`, `payload` | a DAG node began |
| `node_finished` | `task_id`, `node_id`, `timestamp`, `payload` | a DAG node finished |
| `partial` | `task_id`, `node_id?`, `timestamp`, `payload` | incremental output |
| `suspended` | `task_id`, `node_id`, `timestamp`, `payload` | hit a write gate → `awaiting_confirmation` |
| `done` | `task_id`, `timestamp`, `payload` | terminal — carries `tasks.result`; socket then closes |

Dropping the WS leaves the poll path intact — the task keeps running and
`GET /tasks/{id}` still reaches the terminal result.

---

## `GET /health`

Unauthenticated liveness probe.

- **Status:** `200` — `{ "status": "ok" }`

---

## Appendix — the tool registry

The planner may only emit tools in this registry (a hallucinated tool name fails the
planner's registry guard). `*` marks **write-gated** tools — the executor suspends
*before* calling them and waits for `confirm`.

| Service | Tools |
|---|---|
| Gmail | `gmail.search_emails`, `gmail.get_email`, `gmail.send_email`*, `gmail.draft_email`, `gmail.update_labels`* |
| GCal | `gcal.search_events`, `gcal.get_event`, `gcal.create_event`*, `gcal.update_event`*, `gcal.delete_event`* |
| Drive | `drive.search_files`, `drive.get_file`, `drive.share_file`*, `drive.create_folder`, `drive.move_file`* |
| Cross-service | `conflict.detect` |

**Write-gated set** (`backend/agents/__init__.py::WRITE_TOOLS`): `gmail.send_email`,
`gmail.update_labels`, `gcal.create_event`, `gcal.update_event`, `gcal.delete_event`,
`drive.share_file`, `drive.move_file`.

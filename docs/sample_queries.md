# Sample Queries & Expected Outputs

Twelve worked queries — **3 single-service, 2 multi-service, 3 hard cases, plus a
write-gate confirm/resume flow, conflict detection, and graceful degradation** —
each with the expected `Intent`, the planned DAG shape, and the synthesized answer.

## How to read this

Every query is submitted the same way — `POST /api/v1/query` returns `202` with a
`task_id`, then you poll `GET /api/v1/tasks/{task_id}` until `status` is terminal
(`success` / `awaiting_confirmation` / `failed`). See [API.md](../API.md) for the
full contract.

```bash
# Enqueue (assumes $TOKEN from POST /login/access-token)
curl -s -XPOST localhost:8000/api/v1/query \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"<the query>"}'
# → { "task_id": "…", "status": "queued", "conversation_id": "…" }

# Poll
curl -s localhost:8000/api/v1/tasks/<task_id> -H "authorization: Bearer $TOKEN"
```

**Determinism note.** The `Intent` JSON below is the exact contract the classifier
emits (structural fields are asserted by the hermetic test suite). The synthesized
`response` prose is **illustrative** — an LLM is not byte-deterministic even at
`temperature=0`, so it is never asserted verbatim; only its structure
(`actions_taken`, `pending_actions`) and the resolved entities are. Under
`EMBED_MODE=fake`, retrieval is fully reproducible (deterministic vectors); under
`EMBED_MODE=real` the same corpus is re-embedded with Gemini.

**Seeded corpus** (from `backend/providers/mock/_corpus_data.py`, dates relative to
seed time — `now`): a Turkish Airlines booking email (**PNR TK4471**), two
`sarah@company.com` **Q3 budget** emails, an **Acme Corp** agenda email + a
`tomorrow` calendar event + a partnership `.docx`, a **"proposal"** email thread
(`mike@partner.com`), two `.pdf` files modified **last month**, an **out-of-office
`.docx`** covering next week, next-week calendar events, and **two distinct "John"
contacts** (John **Smith** / John **Doe**) each with an email and a meeting.

---

## Single-service

### 1 · "What's on my calendar next week?"

Single service (`gcal`); temporal phrase resolved to an explicit tz range.

**Intent**
```json
{ "services": ["gcal"], "intent": "check_calendar",
  "entities": { "timeframe_phrase": "next week",
                "timeframe": { "start": "…T00:00:00-05:00", "end": "…T23:59:59.999999-05:00" } },
  "steps": ["search_events_next_week"], "needs_clarification": false }
```

**DAG** — 1 node, `gcal.search_events`, `depends_on: []`. The `timeframe` becomes a
`start_at`/`end_at` SQL prefilter on `gcal_datasource`.

**Response** (illustrative)
> You have 3 events next week: **Team Standup** (Mon 9:00), **Product Review**
> (Tue 14:00), and a **Client Call** (Tue 11:00).

---

### 2 · "Find emails from sarah@company.com about the budget"

Single service (`gmail`); sender + topic pulled verbatim from the query.

**Intent** (fixture `single_gmail.json`)
```json
{ "services": ["gmail"], "intent": "search_emails",
  "entities": { "sender": "sarah@company.com", "topic": "budget" },
  "steps": ["search_emails_from_sarah_about_budget"], "needs_clarification": false }
```

**DAG** — 1 node, `gmail.search_emails`, args `{ "sender": "sarah@company.com" }`.
`sender` is a btree prefilter on `gmail_datasource.sender_email_id`; "budget" drives
the cosine ranking.

**Response** (illustrative)
> I found 2 emails from **sarah@company.com** about the budget: **"Q3 Budget
> Review"** (8% under on marketing spend) and **"Re: Q3 Budget Review — updated
> figures"** (total now 1.2M with a 50k contingency, awaiting your approval).

---

### 3 · "Show me PDFs in Drive from last month"

Single service (`drive`); mime + "last month" range.

**Intent** (fixture `single_drive.json`)
```json
{ "services": ["drive"], "intent": "search_files",
  "entities": { "file_type": "PDF", "timeframe_phrase": "last month",
                "timeframe": { "start": "…", "end": "…" } },
  "steps": ["search_drive_for_pdfs_last_month"], "needs_clarification": false }
```

**DAG** — 1 node, `drive.search_files`, args carry the `application/pdf` mime filter
+ the resolved `modified_at` range.

**Response** (illustrative)
> I found 2 PDFs modified last month: **"Q3 Financial Report.pdf"** (revenue +12%
> QoQ) and **"Vendor Contract Draft.pdf"** (for legal review).

---

## Multi-service

### 4 · "Cancel my Turkish Airlines flight"  (Gmail + GCal)

Multi-service; implies a write, so the executor will gate the mutating step.

**Intent** (fixture `multi_cancel.json`)
```json
{ "services": ["gmail", "gcal"], "intent": "cancel_flight",
  "entities": { "airline": "Turkish Airlines" },
  "steps": ["find_flight_email", "delete_calendar_event", "draft_cancellation_email"],
  "needs_clarification": false }
```

**DAG** — a **sequential chain**: `gmail.search_emails` (find the booking) →
`gcal.search_events` (find the flight event) → a write-gated node
(`gcal.delete_event`* / `gmail.send_email`*) whose args carry a deferred ref like
`"$n1.booking_ref"` (resolved by the executor's extractor from the email body).

**Outcome.** Reads run and resolve **PNR TK4471** from the booking email; the
mutating step is **write-gated** → the task ends `awaiting_confirmation` with a
`pending_actions` preview (see [#10](#10--write-gate--confirm--resume-lifecycle)).
The mock corpus has the booking email but no matching calendar event, so the
calendar leg returns empty and synth notes it — a natural graceful-degradation case.

**Response** (illustrative)
> I found your **Turkish Airlines** booking (**PNR TK4471**, flight TK0001
> IST→JFK) in an email from reservations@turkishairlines.com. I couldn't find a
> matching calendar event. I've drafted a cancellation request — **would you like me
> to send it?**

---

### 5 · "Prepare for tomorrow's meeting with Acme Corp"  (GCal + Gmail + Drive)

Multi-service; three **independent** reads in one parallel layer.

**Intent** (fixture `multi_prepare.json`)
```json
{ "services": ["gcal", "gmail", "drive"], "intent": "prepare_for_meeting",
  "entities": { "org": "Acme Corp", "timeframe_phrase": "tomorrow",
                "timeframe": { "start": "…", "end": "…" } },
  "steps": ["find_tomorrow_meeting", "find_emails_acme", "find_drive_files_acme"],
  "needs_clarification": false }
```

**DAG** — 3 sibling nodes, all `depends_on: []` (same parallel layer, run with
`asyncio.gather`): `gcal.search_events`, `gmail.search_emails`, `drive.search_files`.
No false ordering is imposed.

**Response** (illustrative)
> Here's your prep for the **Meeting with Acme Corp** tomorrow at 10:00 (Zoom):
> ✓ Agenda email from john.doe@acmecorp.com (partnership scope, pricing, timeline)
> ✓ Drive doc **"Acme Corp Partnership Overview.docx"** (joint go-to-market, tiered
> pricing).

---

## Hard cases

### 6 · "Move the meeting with John"  → clarification

Ambiguous on two axes (which John? which meeting?). The classifier **flags** rather
than guesses — and because two distinct "John" contacts are seeded (John Smith,
John Doe), this is genuinely ambiguous.

**Intent** (fixture `hard_ambiguous.json`)
```json
{ "services": [], "intent": "ambiguous_request", "entities": {}, "steps": [],
  "needs_clarification": true,
  "clarification": "Which meeting with John do you want to move, and to what time?" }
```

**Outcome.** `needs_clarification: true` **short-circuits the pipeline** — no plan is
built, no DAG runs. The task ends `success` with the clarification as its `response`
and **no** `actions_taken`. (The suite asserts only the flag, never the wording.)

**Response**
> Which meeting with John do you want to move, and to what time? I see a **1:1 with
> John Smith** and a **Sync with John Doe**.

---

### 7 · "That email about the proposal"  → conversation context

Context-dependent — the same query resolves **differently** depending on whether a
prior turn mentioned a proposal. This exercises the last-5-turn context injection.

**7a — empty context** (fixture `hard_proposal_empty.json`)
```json
{ "services": [], "intent": "ambiguous_request", "entities": {}, "steps": [],
  "needs_clarification": true, "clarification": "Which proposal are you referring to?" }
```
> Which proposal are you referring to?

**7b — with prior "proposal" turn in context** (fixture `hard_proposal_context.json`)
```json
{ "services": ["gmail"], "intent": "get_email",
  "entities": { "topic": "the proposal" },
  "steps": ["find_email_about_proposal_from_context"], "needs_clarification": false }
```
**DAG** — 1 node, `gmail.search_emails`/`gmail.get_email` for the proposal thread.
> I found the **"Project proposal draft"** thread from mike@partner.com — the latest
> reply says the proposal is ready for sign-off.

The rolling context lives in Redis (`user:{uid}:conv:{conversation_id}`); the
classifier reads it to resolve the referent.

---

### 8 · "Next Tuesday"  → temporal reasoning + timezone

A bare temporal phrase (as a follow-up turn). The one place an **exact** value is
asserted, because the range is deterministic given `(now, tz)`.

**Intent** (fixture `hard_next_tuesday.json`, then enriched by `resolve_timeframe`)
```json
{ "services": ["gcal"], "intent": "check_calendar",
  "entities": { "timeframe_phrase": "next tuesday",
                "timeframe": { "start": "2026-01-13T00:00:00-05:00",
                               "end":   "2026-01-13T23:59:59.999999-05:00" } },
  "steps": ["check_calendar_next_tuesday"], "needs_clarification": false }
```

The exact range depends on the clock + `users.timezone`. Example above assumes
`now = Wed 2026-01-07`, tz `America/New_York` (EST, `-05:00`) → the upcoming Tuesday
is `2026-01-13`, midnight-to-end-of-day. A DST-boundary date is included in the test
matrix to catch off-by-one-hour bugs.

**Response** (illustrative)
> On **Tuesday, Jan 13** you have a **Product Review** at 14:00 and a **Client
> Call** at 11:00.

---

## Bonus flows

### 9 · "Find events next week that conflict with my out-of-office doc"  → conflict detection

Multi-service (`gcal` + `drive`) feeding the `conflict.detect` tool.

**Intent** (fixture `multi_conflict.json`)
```json
{ "services": ["gcal", "drive"], "intent": "detect_conflicts",
  "entities": { "timeframe_phrase": "next week", "topic": "out-of-office doc" },
  "steps": ["get_ooo_doc_dates", "search_events_next_week", "detect_overlap"],
  "needs_clarification": false }
```

**DAG** — `drive.search_files` (fetch the OOO doc) **∥** `gcal.search_events` (next
week) are one parallel layer; the `conflict.detect` node `depends_on` **both**. The
extractor derives the OOO window (`next_week … next_week+4d`) from the doc text and
computes interval overlaps against the events.

**Response** (illustrative)
> Heads up — 3 events next week fall inside your out-of-office window
> (`<start>`–`<end>`): **Team Standup**, **Product Review**, and **Client Call**.
> You may want to reschedule them.

> Non-overlapping edge case: if no event intersects the window, `conflict.detect`
> returns an empty list and synth reports "no conflicts" — no crash.

---

### 10 · Write-gate → confirm → resume lifecycle

Any mutating tool suspends **before** executing. Demonstrated with a clean delete.

**Turn 1 — "Cancel my Team Standup next week."**
`gcal.search_events` finds the event → `gcal.delete_event`* is **write-gated**.

Poll `GET /tasks/{T1}`:
```jsonc
{ "id": "T1", "kind": "query", "status": "awaiting_confirmation",
  "result": {
    "response": "I found your Team Standup next week. Delete it?",
    "actions_taken": [ { "tool": "gcal.search_events", "status": "executed" } ],
    "pending_actions": [
      { "action_id": "A1", "tool": "gcal.delete_event",
        "args": { "event_id": "nextweek-standup" },
        "preview": "Delete calendar event 'Team Standup'" } ] } }
```

**Turn 2 — confirm** (`POST /query` with `confirm`):
```json
{ "query": "Yes, delete it.",
  "conversation_id": "<same>",
  "confirm": { "action_id": "A1", "decision": "approved" } }
```
Returns a **new** `task_id` (T2) with `parent_task_id = T1`. It resumes from the
checkpoint, executes the gated tool (the mock marks `actions_log.status=executed` —
never mutates the corpus), and ends `success`:
```jsonc
{ "id": "T2", "kind": "confirm", "status": "success", "parent_task_id": "T1",
  "result": { "response": "Done — I deleted the Team Standup event.",
              "actions_taken": [ { "tool": "gcal.delete_event", "status": "executed" } ] } }
```

**Deny path** — `decision: "denied"` → same shape, **no write**, the action is logged
`actions_log.status=denied`, and synth confirms nothing was changed.

---

### 11 · Graceful degradation (partial failure)

Cross-service failures degrade rather than fail the whole turn. A node marked
`optional` that errors or returns empty is **skipped**; the task still ends
`success` and synth **names the degraded service**.

**"Prepare for the Acme meeting"** with the Gmail leg forced to fail:
```jsonc
{ "id": "…", "status": "success",
  "result": {
    "response": "I pulled your Acme calendar event and the partnership doc. I
                 couldn't reach Gmail this time, so I don't have the agenda email.",
    "actions_taken": [
      { "tool": "gcal.search_events", "status": "executed" },
      { "tool": "drive.search_files", "status": "executed" },
      { "tool": "gmail.search_emails", "status": "failed" } ] } }
```

A **required** node returning empty/ambiguous instead triggers ≤1 bounded re-plan or
a clarification question — never a silent wrong guess.

---

## Coverage summary

| # | Query | Services | Category | Notable behavior |
|---|---|---|---|---|
| 1 | calendar next week | gcal | single | temporal range resolution |
| 2 | emails from sarah about budget | gmail | single | sender + topic prefilter |
| 3 | PDFs from last month | drive | single | mime + date filter |
| 4 | cancel Turkish Airlines flight | gmail+gcal | multi | sequential chain + write gate + degradation |
| 5 | prepare for Acme meeting | gcal+gmail+drive | multi | 3-way parallel reads |
| 6 | move the meeting with John | — | hard | ambiguity → clarification (no DAG) |
| 7 | that email about the proposal | gmail / — | hard | conversation-context resolution |
| 8 | next Tuesday | gcal | hard | tz-exact date range |
| 9 | events conflicting with OOO doc | gcal+drive | bonus | conflict detection (interval overlap) |
| 10 | cancel Team Standup → confirm | gcal | bonus | write-gate suspend → resume (`parent_task_id`) |
| 11 | Acme prep with Gmail down | gcal+drive | bonus | graceful degradation, still `success` |

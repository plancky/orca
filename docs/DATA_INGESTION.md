# Data Ingestion, Chunking & Storage

Deep-dive on how raw Gmail / Calendar / Drive items become searchable vectors: the
two-table **datasource → vector store** storage model, the per-service **chunking
strategies**, and the **sync/embed pipeline** that fills them.

> Source of truth for the schema and pipeline is [`PLAN.md`](./PLAN.md) (§ *Data model &
> migrations*, § *Component design → Embedder / Hybrid search*, § *Celery beat & workers*).
> This document expands the ingestion + chunking rationale; if the two ever disagree,
> `PLAN.md` wins.

---

## 1. Storage model: datasource vs. vector store

Each service is split into **two tables** — a canonical record and its chunked embeddings —
rather than one denormalized row. This is the classic **document/chunk split**.

```
USERS ─< GMAIL_DATASOURCE  ─< GMAIL_VECTOR_STORE   (N chunks per email)
      ─< GCAL_DATASOURCE   ─< GCAL_VECTOR_STORE    (usually 1 chunk per event)
      ─< GDRIVE_DATASOURCE ─< GDRIVE_VECTOR_STORE   (many chunks per file)
```

### `*_datasource` — the canonical record (no vectors)

One row **per real-world item** (email / event / file). Holds the full content + all
metadata; it is the **source of truth** the synthesizer reads from and the target of every
metadata filter.

| Table | Key columns |
|---|---|
| `gmail_datasource` | `user_id` FK, `email_id`, `thread_id`, `sender_email_id`, `receiver_email_id`, `subject`, `content` TEXT, `labels[]`, `sent_at`, `received_at`, `UNIQUE(user_id, email_id)` |
| `gcal_datasource` | `user_id` FK, `event_id`, `title`, `description` TEXT, `location`, `start_at`, `end_at`, `attendees[]` *(metadata-only)*, `UNIQUE(user_id, event_id)` |
| `gdrive_datasource` | `user_id` FK, `file_id`, `name`, `mime_type`, `content` TEXT *(full extracted text)*, `owner`, `modified_at`, `UNIQUE(user_id, file_id)` |

### `*_vector_store` — the embedding index (one row per chunk)

One row **per chunk**, `N:1` back to its datasource parent. Carries only what search needs.

| Column | Purpose |
|---|---|
| `id` PK | chunk id |
| `datasource_id` FK → `*_datasource.id` **NOT NULL**, `ON DELETE CASCADE` | parent item; deleting the item drops its chunks |
| `user_id` *(denormalized)* | lets the user-scope prefilter run `WHERE user_id = …` on the chunk table with **no join** |
| `thread_id` *(gmail only, denormalized)* | thread reconstruction without a join |
| `chunk_index` | order within the item; `UNIQUE(datasource_id, chunk_index)` |
| `chunk_text` TEXT | the exact text that was embedded (kept for rerank + debugging) |
| `token_count` | chunk size accounting |
| `embedding vector(1024)` | BGE/GTE-large embedding (`EMBED_DIM=1024`) |

### Why split?

- **Re-chunking is cheap & safe.** Changing a chunking strategy or embedding model rebuilds
  only `*_vector_store` rows for an item; the canonical `*_datasource` content is never touched.
- **Search ranks fine-grained chunks, returns whole items.** Chunk-level recall (hit the
  *relevant section* of a 40-page doc) + parent-level context (synthesize over the full item).
- **One item can't flood the top-N.** Ranking collapses chunks back to their parent
  (see § 4).
- **Clean filter placement.** Rich metadata filters live once on `*_datasource`; only the hot
  `user_id` scope is denormalized onto chunks.

### Denormalization contract

`user_id` (and `thread_id` for Gmail) is intentionally duplicated onto the chunk row. It is
**derived from the parent** at write time and never edited independently — the sync beat is
the single writer, so the copies cannot drift.

---

## 2. Chunking strategies (three, not one)

An email, a calendar event, and a Drive file have wildly different structure and size, so
each service gets its **own** chunker. Common target: **512-token** chunks with **64-token
overlap** where windowing is needed.

### 2.1 Gmail — per-message, thread-aware

- **Unit:** the individual **message** (not the whole thread). Bounded chunk size; the thread
  is still reconstructable via `thread_id` (denormalized onto both tables).
- **Clean first:** strip **quoted reply history** (`On <date>, <X> wrote: > …`) and the
  **signature** before embedding — quoted text duplicated across a thread otherwise poisons
  similarity with near-identical vectors.
- **Embed text:** `subject + "\n" + cleaned_body`.
- **Split:** short email → **1 chunk**; long body → **512-token windows, 64-token overlap**.
- **At synthesis:** a hit resolves to its message; synth can pull sibling messages by
  `thread_id` to reconstruct the conversation ("that email about the proposal" = a thread).

### 2.2 GCal — atomic (mostly no chunking)

- **Unit:** the whole **event**. Events are tiny.
- **Embed text:** `title + description + location` as **1 chunk**.
- **Split:** only if `description` exceeds the token window (pasted agendas/notes) → fall back
  to 512/64 windows.
- **Attendees are metadata-only.** They are **not** embedded — attendee matching is a precise
  structured filter (`attendees[]` on `gcal_datasource`), not a fuzzy semantic one.

### 2.3 GDrive — recursive structural chunking (the real chunking problem)

- **Unit:** **sections** of the file. A PDF/Doc can be tens of thousands of tokens — this is
  where chunking earns its keep.
- **Extract:** full text from the file (`content` on `gdrive_datasource`).
- **Split:** **recursive structural split** on natural boundaries —
  **headings → paragraphs → sentences** — with a **512-token target** and **64-token overlap**,
  so a semantic query hits the *relevant section*, not a diluted whole-file vector.
- **Neighbor widening:** each chunk keeps its `chunk_index`; on a hit, synth can widen to
  adjacent chunks via `datasource_id + chunk_index ± 1` to recover surrounding context.

### Strategy summary

| Service | Unit | Embed text | Split rule | Notes |
|---|---|---|---|---|
| **Gmail** | message | `subject + "\n" + cleaned_body` | 1 chunk; long → 512/64 | strip quoted history + signature; `thread_id` denormalized |
| **GCal** | event | `title + description + location` | 1 chunk; split only if oversized | attendees metadata-only |
| **GDrive** | section | recursive structural text | headings→paragraphs→sentences, 512/64 | neighbor-widen via `chunk_index±1` |

---

## 3. Ingestion pipeline (the 15-min sync beat)

Ingestion and embedding happen in **one pass** — there is no separate embed cron. A single
Celery beat (`workers/sync.py`, `SYNC_BEAT_MINUTES=15`) runs per active user (plus manual
`POST /sync/trigger`):

```
sync beat (per active user, every 15 min)
  ├─ fetch new/changed items
  │     mock:    refresh seed deltas
  │     Google:  incremental via historyId / syncToken / pageToken   (Phase 2)
  ├─ upsert  *_datasource            (canonical record; UNIQUE(user_id, item_id) → idempotent)
  ├─ chunk   (per-service strategy, § 2)
  ├─ embed   each chunk inline       (batched ~64 through the embedder)
  ├─ write   *_vector_store rows     (replace this item's chunk rows on re-chunk)
  ├─ delete  removed items           (CASCADE drops their chunks)
  └─ update  sync_status(last_synced_at, item_count, cursor)
```

- **Searchable immediately.** Because embed happens in the same pass, an item is discoverable
  the moment the beat commits it — freshness target **< 15 min**.
- **Idempotent upserts.** `UNIQUE(user_id, item_id)` on the datasource makes re-sync an update,
  not a duplicate. Incremental cursors (`sync_status.cursor`) drive delta sync in Phase 2.
- **Re-chunk = replace.** Rewriting an item's chunks deletes and re-inserts only its
  `*_vector_store` rows; `*_datasource` is untouched.

### Corpus vs. query embeddings

Two embedding call sites, only one is cached:

| Path | When | Where vectors live | Cache |
|---|---|---|---|
| **Corpus** | background sync beat | pgvector (`*_vector_store`) | **not** Redis-cached |
| **Query** | inline, hot path, per `search()` | ephemeral | Redis `user:{user_id}:emb:{sha256(text)}|model`, 1h TTL, per-user |

The **BGE query instruction prefix**
(`"Represent this sentence for searching relevant passages:"`) is applied **only on the query
side** at search time (configurable) — never to corpus chunks.

---

## 4. How storage + chunking serve retrieval

Hybrid search **ranks chunks** and **returns items** (full detail in `PLAN.md` §
*Hybrid search*):

```
embed(query)                                   ← BGE query prefix + Redis cache
  └─ SQL prefilter
        WHERE vs.user_id = :uid                 ← chunk table, NO join (denormalized user_id)
        JOIN  *_datasource ds ON ds.id = vs.datasource_id
        AND   ds.sender_email_id = … / ds.sent_at IN range / labels …   ← metadata on datasource
     ORDER BY vs.embedding <=> :q               ← cosine over chunks
  └─ collapse to parent
        DISTINCT ON (vs.datasource_id) best chunk score   ← one row per item
  └─ [rerank] → recency decay  score * exp(-λ·age)
  └─ top-N items → synth reads full ds.content
```

- **`user_id` filter → chunk table, no join** (denormalized); **rich filters → datasource**,
  joined in.
- **Collapse to parent** prevents a single email/file from occupying multiple top-N slots with
  its own chunks.
- **Synthesis reads the canonical item**, not the chunk: full `content` from `*_datasource`,
  Gmail threads widened via `thread_id`, large GDrive hits widened via `chunk_index ± 1`.

---

## 5. ORM shape (SQLModel)

Each service follows the same two-class split (`table=True`), mirroring the pattern in
`PLAN.md`:

```python
class GmailDatasource(SQLModel, table=True):
    # canonical columns: user_id, email_id, thread_id, sender_email_id,
    # receiver_email_id, subject, content, labels, sent_at, received_at
    chunks: list["GmailChunk"] = Relationship(
        back_populates="datasource",
        sa_relationship_kwargs={"lazy": "selectin"},   # async-safe eager load
    )

class GmailChunk(SQLModel, table=True):                # → gmail_vector_store
    datasource_id: uuid.UUID = Field(foreign_key="gmaildatasource.id", ondelete="CASCADE")
    user_id: uuid.UUID                                 # denormalized
    thread_id: str                                     # denormalized
    chunk_index: int
    chunk_text: str
    token_count: int
    embedding: list[float] = Field(sa_column=Column(Vector(1024)))
```

`GCal` and `GDrive` mirror this exactly (`GCalDatasource`/`GCalChunk`,
`GDriveDatasource`/`GDriveChunk`). `*Public` response variants expose datasource metadata and
**never** the raw `embedding`.

---

## 6. Indexes

| Index | Table | Purpose |
|---|---|---|
| HNSW `vector_cosine_ops` (fallback IVFFlat) | `*_vector_store.embedding` | cosine ANN over chunks |
| btree `(user_id)` | `*_vector_store` | no-join user-scope prefilter |
| btree `(datasource_id)` | `*_vector_store` | parent-collapse join |
| btree `(user_id, received_at / start_at / modified_at)` | `*_datasource` | recency + date-range prefilter |
| btree `(sender_email_id)`, `(attendees)` | `*_datasource` | metadata prefilter |

All queries are scoped `WHERE user_id = …`; `DESIGN.md` describes partition-by-`user_id` for
scale.

---

## 7. Verification

From `PLAN.md` § *Verification* — the ingestion-specific check:

> **Sync + embed beat:** add a new seed item → trigger the 15-min sync beat (or
> `POST /sync/trigger`) → assert the item lands in `*_datasource` **and** ≥1 `*_vector_store`
> chunk row with a non-null `embedding` (FK back to the datasource) in that one pass and is
> immediately discoverable via `/query`; `GET /sync/status` shows the updated
> `last_synced_at` + `item_count`.

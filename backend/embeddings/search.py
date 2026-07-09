"""Hybrid pgvector search: rank chunks, return parent items.

Ranks fine-grained ``*_vector_store`` chunks by cosine distance (``<=>``,
magnitude-invariant, so Gemini's sub-native-dim vectors need no renormalization)
under a user-scope prefilter plus optional metadata filters, then collapses to
one row per ``*_datasource`` parent (``DISTINCT ON``) keeping the best chunk
score, and applies a recency decay. Returns datasource-level items with a score
(DATA_INGESTION §4, PLAN.md §Hybrid search).

Built with ``text()`` and a raw-list bound param on purpose: the frozen
``db.session`` registers pgvector's asyncpg binary codec (``register_vector``),
which encodes a Python list directly — whereas the ORM ``VECTOR`` column type's
bind processor would stringify the list and the codec would reject it. Passing
the embedding as a plain list param is the binding that both paths agree on.

Rerank is intentionally NOT invoked here — the frozen ``hybrid_search`` signature
carries the query *embedding*, not the query *text* that ``reranker.rerank``
needs; the pipeline layer applies rerank (disabled by default) after this call.
"""

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# service -> (chunk table, datasource table, recency timestamp column)
_SERVICES: dict[str, tuple[str, str, str]] = {
    "gmail": ("gmail_vector_store", "gmail_datasource", "received_at"),
    "gcal": ("gcal_vector_store", "gcal_datasource", "start_at"),
    "gdrive": ("gdrive_vector_store", "gdrive_datasource", "modified_at"),
}

# Per-service datasource columns eligible for metadata filtering + their kind.
# Column names come from this hardcoded spec (never user input), so interpolating
# them into the SQL is injection-safe; all *values* are bound parameters.
_FILTER_COLUMNS: dict[str, dict[str, str]] = {
    "gmail": {
        "email_id": "scalar",
        "thread_id": "scalar",
        "sender_email_id": "scalar",
        "receiver_email_id": "scalar",
        "subject": "scalar",
        "received_at": "range",
        "sent_at": "range",
        "labels": "array",
    },
    "gcal": {
        "event_id": "scalar",
        "title": "scalar",
        "location": "scalar",
        "start_at": "range",
        "end_at": "range",
        "attendees": "array",
    },
    "gdrive": {
        "file_id": "scalar",
        "name": "scalar",
        "mime_type": "scalar",
        "owner": "scalar",
        "modified_at": "range",
    },
}

# Gentle recency decay: 30-day half-life, so cosine relevance stays the dominant
# signal while fresher items get a small lift (score * exp(-lambda * age_days)).
_RECENCY_HALFLIFE_DAYS = 30.0
_RECENCY_LAMBDA = math.log(2) / _RECENCY_HALFLIFE_DAYS


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _coerce_temporal(value: Any) -> Any:
    """ISO-8601 string -> ``datetime`` so asyncpg can bind a ``timestamptz`` param.

    Every ``range`` filter column is a timestamp, and the planner's resolved
    timeframe supplies ``.isoformat()`` strings — but asyncpg binds ``timestamptz``
    from ``datetime`` only and raises ``DataError`` on a ``str``. Non-string or
    unparseable values pass through untouched.
    """
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value


def _build_filter_sql(
    service: str, filters: dict[str, Any] | None
) -> tuple[list[str], dict[str, Any]]:
    """Translate a planner-supplied filter dict into ``ds.*`` SQL clauses.

    Only keys that map to a real filterable column are applied; unknown keys
    (an LLM can hallucinate them) are ignored rather than erroring.
    """
    spec = _FILTER_COLUMNS[service]
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if not filters:
        return clauses, params

    for key, value in filters.items():
        if value is None:
            continue
        # Range convenience suffixes: <col>_after / <col>_before.
        if key.endswith("_after") and spec.get(key[:-6]) == "range":
            col, pname = key[:-6], f"f_{key}"
            clauses.append(f"ds.{col} >= :{pname}")
            params[pname] = _coerce_temporal(value)
            continue
        if key.endswith("_before") and spec.get(key[:-7]) == "range":
            col, pname = key[:-7], f"f_{key}"
            clauses.append(f"ds.{col} <= :{pname}")
            params[pname] = _coerce_temporal(value)
            continue

        kind = spec.get(key)
        if kind == "scalar":
            clauses.append(f"ds.{key} = :f_{key}")
            params[f"f_{key}"] = value
        elif kind == "range" and isinstance(value, dict):
            if value.get("start") is not None:
                clauses.append(f"ds.{key} >= :f_{key}_start")
                params[f"f_{key}_start"] = _coerce_temporal(value["start"])
            if value.get("end") is not None:
                clauses.append(f"ds.{key} <= :f_{key}_end")
                params[f"f_{key}_end"] = _coerce_temporal(value["end"])
        elif kind == "range":
            clauses.append(f"ds.{key} = :f_{key}")
            params[f"f_{key}"] = _coerce_temporal(value)
        elif kind == "array":
            vals = list(value) if isinstance(value, (list, tuple, set)) else [value]
            clauses.append(f"ds.{key} && :f_{key}")  # postgres array overlap
            params[f"f_{key}"] = vals
    return clauses, params


def _recency_multiplier(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return math.exp(-_RECENCY_LAMBDA * age_days)


async def hybrid_search(
    session: AsyncSession,
    query_embedding: list[float],
    service: str,
    user_id: str,
    filters: dict | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Rank chunks by cosine distance, collapse to parents, decay by recency.

    ``query_embedding`` is bound as a raw pgvector list param. The user scope
    runs on the chunk table with no join (denormalized ``user_id``); metadata
    filters run on the joined datasource. One row per datasource is returned,
    ordered by recency-decayed cosine similarity, capped at ``top_k``.
    """
    if service not in _SERVICES:
        raise ValueError(f"unknown service: {service!r}")
    vs_table, ds_table, recency_col = _SERVICES[service]

    filter_clauses, filter_params = _build_filter_sql(service, filters)
    where_sql = " AND ".join(["vs.user_id = :uid", *filter_clauses])
    # DISTINCT ON (datasource_id) ordered by distance keeps the best chunk per
    # parent so one item can't flood the top-N with its own chunks.
    sql = text(
        f"SELECT DISTINCT ON (vs.datasource_id) "
        f"       ds.*, vs.chunk_text AS chunk_text, "
        f"       (vs.embedding <=> :q) AS distance "
        f"FROM {vs_table} vs "
        f"JOIN {ds_table} ds ON ds.id = vs.datasource_id "
        f"WHERE {where_sql} "
        f"ORDER BY vs.datasource_id, vs.embedding <=> :q"
    )
    params: dict[str, Any] = {
        "uid": _as_uuid(user_id),
        "q": query_embedding,
        **filter_params,
    }
    rows = (await session.execute(sql, params)).mappings().all()

    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for row in rows:
        item = dict(row)
        distance = float(item["distance"])
        similarity = 1.0 - distance
        item["datasource_id"] = item.get("id")
        item["distance"] = distance
        item["similarity"] = similarity
        item["score"] = similarity * _recency_multiplier(
            item.get(recency_col), now
        )
        items.append(item)

    # Cosine collapse is ordered by datasource_id; re-rank the collapsed items by
    # recency-decayed similarity (stable, deterministic given fixed inputs).
    items.sort(key=lambda it: it["score"], reverse=True)
    return items[:top_k]


async def filter_search(
    session: AsyncSession,
    service: str,
    user_id: str,
    filters: dict | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Filter-and-sort over a datasource table — no embedding, no vector store.

    The path for queries with no semantic term (e.g. "meetings last week"): there
    is nothing to rank by relevance, so we skip the embedder and the
    ``*_vector_store`` entirely and read ``*_datasource`` directly under the user
    scope + the same metadata filters ``hybrid_search`` accepts (date ranges,
    scalars, arrays), ordered by the service's recency column (newest first).

    Returns datasource-level items shaped like ``hybrid_search`` (``datasource_id``
    + a ``score``), with ``distance``/``similarity`` set to ``None`` because no
    cosine ranking took place. ``score`` is the recency multiplier alone, so
    downstream ordering stays consistent.
    """
    if service not in _SERVICES:
        raise ValueError(f"unknown service: {service!r}")
    _vs_table, ds_table, recency_col = _SERVICES[service]

    filter_clauses, filter_params = _build_filter_sql(service, filters)
    where_sql = " AND ".join(["ds.user_id = :uid", *filter_clauses])
    sql = text(
        f"SELECT ds.* FROM {ds_table} ds "
        f"WHERE {where_sql} "
        f"ORDER BY ds.{recency_col} DESC NULLS LAST "
        f"LIMIT :limit"
    )
    params: dict[str, Any] = {
        "uid": _as_uuid(user_id),
        "limit": top_k,
        **filter_params,
    }
    rows = (await session.execute(sql, params)).mappings().all()

    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for row in rows:
        item = dict(row)
        item["datasource_id"] = item.get("id")
        item["distance"] = None
        item["similarity"] = None
        item["score"] = _recency_multiplier(item.get(recency_col), now)
        items.append(item)
    return items

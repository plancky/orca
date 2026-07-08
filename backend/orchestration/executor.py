import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from backend.agents import WRITE_TOOLS
from backend.config import settings
from backend.db.models import ActionsLog, ActionStatus, Task, TaskStatus
from backend.llm.client import llm_client
from backend.llm.json_utils import extract_and_validate
from backend.llm.prompts.extractor import ExtractedValue, build_extractor_prompt
from backend.orchestration.models.dag import (
    DEFERRED_REF_RE,
    DeferredArgResolutionError,
    Node,
    Plan,
)
from backend.orchestration.models.intent import Intent
from backend.orchestration.models.progress import (
    NodeFinishedEvent,
    NodeStartedEvent,
    ProgressEvent,
    SuspendedEvent,
)
from backend.orchestration.models.results import (
    PendingAction,
)
from backend.orchestration.utils.checkpoint import Checkpoint
from backend.orchestration.utils.tools import get_tool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _traverse(obj: Any, path: str) -> Any:
    parts = path.split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list):
            if p.isdigit() and int(p) < len(cur):
                cur = cur[int(p)]
            elif len(cur) > 0 and isinstance(cur[0], dict) and p in cur[0]:
                cur = cur[0][p]
            else:
                return None
        else:
            return None
    return cur


async def _extract_deferred(context: Any, field_name: str) -> Any:
    messages = build_extractor_prompt(context, field_name)
    response = await llm_client.chat(messages, response_format="json_object")
    extracted = await extract_and_validate(response, ExtractedValue, llm_client)
    return extracted.value


async def _resolve_args(node: Node, node_outputs: dict[str, Any]) -> dict[str, Any]:
    resolved = {}
    for k, v in node.args.items():
        if isinstance(v, str) and DEFERRED_REF_RE.match(v):
            match = DEFERRED_REF_RE.match(v)
            n_idx = match.group(1)
            field_path = match.group(2)
            upstream_id = f"n{n_idx}"
            if upstream_id not in node_outputs:
                if node.optional:
                    resolved[k] = None
                    continue
                else:
                    raise DeferredArgResolutionError(
                        f"Upstream {upstream_id} output not found"
                    )

            upstream_out = node_outputs[upstream_id]

            # (a) direct substitution
            val = _traverse(upstream_out, field_path)
            if val is not None:
                resolved[k] = val
            else:
                # (b) deferred extraction
                val = await _extract_deferred(upstream_out, field_path)
                if val is not None:
                    resolved[k] = val
                else:
                    if node.optional:
                        resolved[k] = None
                    else:
                        raise DeferredArgResolutionError(
                            f"Could not extract {field_path} from {upstream_id}"
                        )
        else:
            resolved[k] = v
    return resolved


async def _publish_progress(
    task_id: str, event: ProgressEvent, session, db_lock: asyncio.Lock
) -> None:
    # Redis
    r = redis.Redis.from_url(settings.REDIS_URL)
    event_dict = json.loads(event.model_dump_json())
    try:
        await r.xadd(f"stream:tasks:{task_id}", {"data": json.dumps(event_dict)})
    finally:
        await r.aclose()

    # DB
    async with db_lock:
        task_row = await session.get(Task, uuid.UUID(task_id))
        if task_row:
            if task_row.progress is None:
                task_row.progress = {}
            # SQLModel/SQLAlchemy doesn't track mutations of JSONB dict
            # automatically unless we assign a new dict or use flag_modified.
            # We will assign a new dict.
            new_prog = dict(task_row.progress)
            new_prog[event.timestamp.isoformat()] = event_dict
            task_row.progress = new_prog
            await session.commit()


async def execute(
    plan: Plan,
    intent: Intent,
    task_id: str,
    user_id: uuid.UUID,
    session,
    *,
    resume_from: Checkpoint | None = None,
) -> dict | Checkpoint:
    # 1. Topo-sort DAG layers
    node_by_id = {n.id: n for n in plan.nodes}
    in_degrees = {n.id: len(n.depends_on) for n in plan.nodes}
    layers = []

    # Simple topo sort into layers
    queue = [nid for nid, deg in in_degrees.items() if deg == 0]
    while queue:
        layers.append(queue)
        next_queue = []
        for qid in queue:
            for nid, node in node_by_id.items():
                if qid in node.depends_on:
                    in_degrees[nid] -= 1
                    if in_degrees[nid] == 0:
                        next_queue.append(nid)
        queue = next_queue

    # Initialize state
    node_outputs = {}
    pending_nodes_to_skip = set()
    if resume_from:
        node_outputs = resume_from.node_outputs

    db_lock = asyncio.Lock()

    async def run_node(node: Node):
        if node.id in pending_nodes_to_skip:
            return None
        try:
            resolved_args = await _resolve_args(node, node_outputs)
        except DeferredArgResolutionError as e:
            if node.optional:
                return {"_error": str(e), "status": "skipped"}
            raise e

        if node.tool in WRITE_TOOLS:
            # We must suspend
            return {"_suspend": True, "node": node, "args": resolved_args}

        await _publish_progress(
            task_id,
            NodeStartedEvent(
                type="node_started",
                task_id=task_id,
                node_id=node.id,
                timestamp=_utcnow(),
                payload={"tool": node.tool, "args": resolved_args},
            ),
            session,
            db_lock,
        )

        try:
            tool_fn = get_tool(node.tool)
            res = await tool_fn(session, user_id, resolved_args)

            await _publish_progress(
                task_id,
                NodeFinishedEvent(
                    type="node_finished",
                    task_id=task_id,
                    node_id=node.id,
                    timestamp=_utcnow(),
                    payload={"result": res},
                ),
                session,
                db_lock,
            )
            return res
        except Exception as e:
            if node.optional:
                return {"_error": str(e), "status": "skipped"}
            raise e

    for layer in layers:
        tasks = []
        for nid in layer:
            if nid in node_outputs:
                continue  # already ran
            tasks.append((nid, run_node(node_by_id[nid])))

        if not tasks:
            continue

        results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)

        for (nid, _), res in zip(tasks, results):
            if isinstance(res, Exception):
                raise res

            if isinstance(res, dict) and res.get("_suspend"):
                # We need to suspend here.
                node = res["node"]
                resolved_args = res["args"]

                # Determine remaining nodes
                all_ids = list(node_by_id.keys())
                # simplistic: any node not in node_outputs and not this node
                remaining = [
                    rid for rid in all_ids if rid not in node_outputs and rid != node.id
                ]

                cp = Checkpoint(
                    intent=intent,
                    plan=plan,
                    node_outputs=node_outputs,
                    pending_node_id=node.id,
                    remaining_node_ids=remaining,
                    context={},
                    resumed_from=None,
                )

                async with db_lock:
                    action_log = ActionsLog(
                        user_id=user_id,
                        task_id=uuid.UUID(task_id),
                        tool=node.tool,
                        args=resolved_args,
                        status=ActionStatus.PENDING.value,
                    )
                    session.add(action_log)
                    await session.commit()
                    await session.refresh(action_log)

                    task_row = await session.get(Task, uuid.UUID(task_id))
                    task_row.checkpoint = json.loads(cp.dump())

                    if task_row.result is None:
                        task_row.result = {}
                    new_result = dict(task_row.result)
                    preview = f"Pending action: {node.tool}"

                    pa = PendingAction(
                        action_id=action_log.id,
                        tool=node.tool,
                        args=resolved_args,
                        preview=preview,
                    )

                    if "pending_actions" not in new_result:
                        new_result["pending_actions"] = []
                    new_result["pending_actions"].append(pa.model_dump(mode="json"))
                    task_row.result = new_result
                    task_row.status = TaskStatus.AWAITING_CONFIRMATION.value
                    await session.commit()

                await _publish_progress(
                    task_id,
                    SuspendedEvent(
                        type="suspended",
                        task_id=task_id,
                        node_id=node.id,
                        timestamp=_utcnow(),
                        payload={"action_id": str(action_log.id)},
                    ),
                    session,
                    db_lock,
                )
                return cp

            node_outputs[nid] = res

    return node_outputs

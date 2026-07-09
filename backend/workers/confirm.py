import asyncio
import logging
import uuid

from sqlalchemy import select

from backend.db.models import ActionsLog, ActionStatus, Task, TaskStatus
from backend.db.session import async_session_factory
from backend.workers.celery_app import app

logger = logging.getLogger(__name__)


async def resume(
    checkpoint_json: str, decision: str, task_id: str, user_id: str
) -> dict:
    """Resume-from-checkpoint pipeline. Wave D1 fills this body."""
    from backend.context.conversation import append_turn_messages
    from backend.llm.client import llm_client
    from backend.orchestration.executor import execute
    from backend.orchestration.utils.checkpoint import Checkpoint
    from backend.orchestration.utils.tools import get_tool
    from backend.synth.synthesizer import synthesize

    checkpoint = Checkpoint.load(checkpoint_json)
    task_uuid = uuid.UUID(task_id)
    user_uuid = uuid.UUID(user_id)

    log_ctx = f"[resume] task_id={task_id} user_id={user_id}"
    logger.info(
        f"{log_ctx} stage=resume status=started decision={decision} "
        f"pending_node_id={checkpoint.pending_node_id}"
    )

    async with async_session_factory() as session:
        task_row = await session.get(Task, task_uuid)
        if not task_row:
            logger.warning(
                f"{log_ctx} stage=resume status=failed error='task not found'"
            )
            return {"error": "Task not found"}

        task_row.status = TaskStatus.RUNNING.value
        await session.commit()

        try:
            action_row = None
            if task_row.parent_task_id:
                stmt = select(ActionsLog).where(
                    ActionsLog.task_id == task_row.parent_task_id,
                    ActionsLog.status == ActionStatus.PENDING.value
                )
                action_row = (await session.execute(stmt)).scalars().first()

            if decision == "approve":
                if action_row:
                    logger.info(
                        f"{log_ctx} stage=resume status=executing_pending_action "
                        f"tool={action_row.tool}"
                    )
                    tool_fn = get_tool(action_row.tool)
                    res = await tool_fn(session, user_uuid, action_row.args)
                    action_row.status = ActionStatus.EXECUTED.value
                    if isinstance(res, dict):
                        action_row.result = res
                    else:
                        action_row.result = {"result": res}
                    await session.commit()
                    checkpoint.node_outputs[checkpoint.pending_node_id] = res
                    logger.info(
                        f"{log_ctx} stage=resume status=pending_action_executed"
                    )
            else:
                logger.info(f"{log_ctx} stage=resume status=pending_action_denied")
                if action_row:
                    action_row.status = ActionStatus.DENIED.value
                    await session.commit()
                checkpoint.node_outputs[checkpoint.pending_node_id] = {
                    "status": "denied",
                    "_error": "User denied action"
                }

            logger.info(f"{log_ctx} stage=execute status=started")
            outcome = await execute(
                checkpoint.plan,
                checkpoint.intent,
                task_id,
                user_uuid,
                session,
                resume_from=checkpoint
            )

            if isinstance(outcome, Checkpoint):
                logger.info(
                    f"{log_ctx} stage=execute status=suspended "
                    f"pending_node_id={outcome.pending_node_id}"
                )
                return {"status": "awaiting_confirmation"}

            logger.info(f"{log_ctx} stage=execute status=finished")

            logger.info(f"{log_ctx} stage=synthesize status=started")
            result = await synthesize(
                checkpoint.intent, outcome, None, llm_client=llm_client
            )
            task_row.result = result.model_dump(mode="json")
            task_row.status = TaskStatus.SUCCESS.value
            await session.commit()
            logger.info(f"{log_ctx} stage=synthesize status=finished")

            if task_row.conversation_id:
                await append_turn_messages(
                    session,
                    task_row.conversation_id,
                    user_uuid,
                    decision,
                    result,
                    task_uuid,
                    intent=checkpoint.intent.model_dump(mode="json"),
                    plan=checkpoint.plan.model_dump(mode="json")
                )

            logger.info(f"{log_ctx} stage=complete status=success")
            return {"status": "success", "result": task_row.result}

        except Exception as e:
            logger.exception(f"{log_ctx} stage=complete status=failed error={e}")
            task_row.status = TaskStatus.FAILED.value
            task_row.error = str(e)
            await session.commit()
            return {"status": "failed", "error": str(e)}


@app.task(name="backend.workers.confirm.run_resume", bind=True)
def run_resume(self, checkpoint_json: str, decision: str, task_id: str, user_id: str):
    logger.info(f"[resume] task_id={task_id} stage=celery_dispatch status=received")
    return asyncio.run(resume(checkpoint_json, decision, task_id, user_id))

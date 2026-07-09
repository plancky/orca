import logging
import uuid

from backend.db.models import Task, TaskStatus
from backend.db.session import async_session_factory
from backend.workers.celery_app import _run, app

logger = logging.getLogger(__name__)


# Module-level importable coroutine — eval/evaluate.py calls this directly,
# bypassing Celery.
async def pipeline(
    task_id: str,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    confirm=None,
) -> dict:
    """Full classify->plan->execute->synth pipeline. Wave D1 fills this body."""
    from backend.context.conversation import append_turn_messages
    from backend.llm.client import llm_client
    from backend.orchestration.executor import execute
    from backend.orchestration.stages.classifier import classify
    from backend.orchestration.stages.planner import plan as plan_stage
    from backend.orchestration.utils.checkpoint import Checkpoint
    from backend.synth.synthesizer import synthesize

    task_uuid = uuid.UUID(task_id)
    user_uuid = uuid.UUID(user_id)
    conv_uuid = uuid.UUID(conversation_id) if conversation_id else None

    log_ctx = f"[pipeline] task_id={task_id} user_id={user_id}"
    logger.info(f"{log_ctx} stage=start query={query!r}")

    async with async_session_factory() as session:
        task_row = await session.get(Task, task_uuid)
        if not task_row:
            logger.warning(f"{log_ctx} stage=start error='task not found'")
            return {"error": "Task not found"}

        task_row.status = TaskStatus.RUNNING.value
        await session.commit()

        try:
            logger.info(f"{log_ctx} stage=classify status=started")
            intent = await classify(
                query,
                context=None,
                now=None,
                tz=None,
                session=session,
                user_id=user_uuid,
            )
            logger.info(
                f"{log_ctx} stage=classify status=finished intent={intent.intent} "
                f"needs_clarification={intent.needs_clarification}"
            )

            if intent.needs_clarification:
                logger.info(f"{log_ctx} stage=classify status=needs_clarification")
                result = await synthesize(intent, {}, None, llm_client=llm_client)
                task_row.result = result.model_dump(mode="json")
                task_row.status = TaskStatus.SUCCESS.value
                await session.commit()
                
                if conv_uuid:
                    await append_turn_messages(
                        session, conv_uuid, user_uuid, query, result, task_uuid,
                        intent=intent.model_dump(mode="json")
                    )
                logger.info(f"{log_ctx} stage=complete status=success (clarification)")
                return {"status": "success", "result": task_row.result}

            logger.info(f"{log_ctx} stage=plan status=started")
            plan_obj = await plan_stage(intent)
            logger.info(
                f"{log_ctx} stage=plan status=finished nodes={len(plan_obj.nodes)}"
            )

            logger.info(f"{log_ctx} stage=execute status=started")
            outcome = await execute(plan_obj, intent, task_id, user_uuid, session)

            if isinstance(outcome, Checkpoint):
                # Suspended (executor already sets status/checkpoint/pending_actions)
                # DO NOT append messages
                logger.info(
                    f"{log_ctx} stage=execute status=suspended "
                    f"pending_node_id={outcome.pending_node_id}"
                )
                return {"status": "awaiting_confirmation"}

            logger.info(f"{log_ctx} stage=execute status=finished")

            logger.info(f"{log_ctx} stage=synthesize status=started")
            result = await synthesize(intent, outcome, None, llm_client=llm_client)
            task_row.result = result.model_dump(mode="json")
            task_row.status = TaskStatus.SUCCESS.value
            await session.commit()
            logger.info(f"{log_ctx} stage=synthesize status=finished")

            if conv_uuid:
                await append_turn_messages(
                    session, conv_uuid, user_uuid, query, result, task_uuid,
                    intent=intent.model_dump(mode="json"),
                    plan=plan_obj.model_dump(mode="json")
                )

            logger.info(f"{log_ctx} stage=complete status=success")
            return {"status": "success", "result": task_row.result}

        except Exception as e:
            logger.exception(f"{log_ctx} stage=complete status=failed error={e}")
            task_row.status = TaskStatus.FAILED.value
            task_row.error = str(e)
            await session.commit()
            return {"status": "failed", "error": str(e)}


@app.task(name="backend.workers.orchestrate.run_pipeline", bind=True)
def run_pipeline(
    self,
    task_id: str,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    confirm=None,
):
    """Sync Celery shell — wraps async pipeline."""
    logger.info(f"[pipeline] task_id={task_id} stage=celery_dispatch status=received")
    return _run(pipeline(task_id, user_id, query, conversation_id, confirm))

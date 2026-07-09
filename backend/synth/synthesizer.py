import json

from pydantic import BaseModel

from backend.llm.json_utils import extract_and_validate
from backend.llm.prompts.synthesizer import SYNTHESIZER_PROMPT
from backend.orchestration.models.intent import Intent
from backend.orchestration.models.results import (
    ActionSummary,
    PendingAction,
    TaskResult,
)


class _SynthLLMResponse(BaseModel):
    response: str
    actions_taken: list[ActionSummary] = []


async def synthesize(
    intent: Intent,
    node_outputs: dict,
    pending_actions: list[PendingAction] | None = None,
    llm_client=None,
) -> TaskResult:
    if intent.needs_clarification:
        return TaskResult(
            response=intent.clarification or "Can you please clarify?",
            actions_taken=[],
            pending_actions=pending_actions,
        )

    if llm_client is None:
        raise ValueError("llm_client is required for synthesis")

    # node_outputs might contain indications of failure, e.g. exceptions
    # but we pass everything to the LLM.
    messages = [
        {"role": "system", "content": SYNTHESIZER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "intent": intent.model_dump(mode="json"),
                    "node_outputs": node_outputs,
                    "pending_actions": [
                        p.model_dump(mode="json") for p in (pending_actions or [])
                    ],
                },
                default=str,
            ),
        },
    ]

    response_text = await llm_client.chat(
        messages, response_format="json_object", temperature=0
    )

    parsed = await extract_and_validate(
        response_text, _SynthLLMResponse, llm_client=llm_client
    )

    return TaskResult(
        response=parsed.response,
        actions_taken=parsed.actions_taken,
        pending_actions=pending_actions,
    )

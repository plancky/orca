"""Classifier JSON-contract suite (query -> Intent).

Tier 1 (default, ``-m "not llm"``): the LLM is stubbed with recorded fixtures,
so every assertion is on the *contract* — the ``services`` set, entity keys/
values literally present in the query, ``needs_clarification`` gating, and the
one deterministic exact-value case (tz-resolved "Next Tuesday"). Tier 2
(``-m llm``) runs the real classifier against ``INFERENCE_BASE_URL`` and checks
the same structural invariants; skipped unless ``GEMINI_STUDIO_API_KEY`` is set.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest

from backend.config import settings
from backend.orchestration.stages.classifier import classify

requires_llm_key = pytest.mark.skipif(
    not settings.GEMINI_STUDIO_API_KEY,
    reason="Tier-2 llm tests need GEMINI_STUDIO_API_KEY + live INFERENCE_BASE_URL",
)


@dataclass(frozen=True)
class Case:
    fixture: str
    query: str
    services: frozenset[str]
    needs_clarification: bool
    literal_entities: tuple[tuple[str, str], ...] = ()
    timeframe_resolved: bool = False
    steps_nonempty: bool = False
    context_pairs: tuple[tuple[str, str], ...] | None = None


_CASES = (
    # --- single service: exactly one service, no clarification ---
    Case(
        "single_gcal", "What's on my calendar next week?", frozenset({"gcal"}),
        False, timeframe_resolved=True, steps_nonempty=True,
    ),
    Case(
        "single_gmail", "Find emails from sarah@company.com about the budget",
        frozenset({"gmail"}), False,
        literal_entities=(("sender", "sarah@company.com"),), steps_nonempty=True,
    ),
    Case(
        "single_drive", "Show me PDFs in Drive from last month",
        frozenset({"drive"}), False,
        literal_entities=(("file_type", "PDF"),), timeframe_resolved=True,
        steps_nonempty=True,
    ),
    # --- multi service: correct set (order-independent), steps present ---
    Case(
        "multi_cancel", "Cancel my Turkish Airlines flight",
        frozenset({"gmail", "gcal"}), False,
        literal_entities=(("airline", "Turkish Airlines"),), steps_nonempty=True,
    ),
    Case(
        "multi_prepare", "Prepare for tomorrow's meeting with Acme Corp",
        frozenset({"gcal", "gmail", "drive"}), False,
        literal_entities=(("org", "Acme Corp"),), timeframe_resolved=True,
        steps_nonempty=True,
    ),
    Case(
        "multi_conflict",
        "Find events next week that conflict with my out-of-office doc",
        frozenset({"gcal", "drive"}), False, timeframe_resolved=True,
        steps_nonempty=True,
    ),
    # --- hard: flag ambiguity instead of guessing; use context ---
    Case("hard_ambiguous", "Move the meeting with John", frozenset(), True),
    Case(
        "hard_proposal_empty", "That email about the proposal", frozenset(),
        True, context_pairs=(),
    ),
    Case(
        "hard_proposal_context", "That email about the proposal",
        frozenset({"gmail"}), False,
        context_pairs=(("user", "Let's review the Acme proposal"),),
    ),
)


def _context(case: Case) -> list[dict] | None:
    if case.context_pairs is None:
        return None
    return [{"role": r, "content": c} for r, c in case.context_pairs]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.fixture)
async def test_classifier_contract(case, stub_llm, frozen_clock, fixed_tz):
    stub_llm.classifier(case.fixture)
    intent = await classify(
        case.query, context=_context(case), now=frozen_clock, tz=ZoneInfo(fixed_tz)
    )

    assert set(intent.services) == set(case.services)
    assert intent.needs_clarification is case.needs_clarification
    assert isinstance(intent.intent, str)
    assert intent.intent

    if case.needs_clarification:
        assert intent.clarification is not None
    for key, value in case.literal_entities:
        assert intent.entities.get(key) == value
        assert value.lower() in case.query.lower()  # literally echoed from query
    if case.timeframe_resolved:
        tf = intent.entities.get("timeframe")
        assert isinstance(tf, dict)
        assert {"start", "end"} <= tf.keys()
    if case.steps_nonempty:
        assert len(intent.steps) > 0


@pytest.mark.asyncio
async def test_classifier_resolves_next_tuesday_exact(
    stub_llm, frozen_clock, fixed_tz
):
    """The one exact-value assertion — deterministic by construction (now, tz)."""
    stub_llm.classifier("hard_next_tuesday")
    intent = await classify("Next Tuesday", now=frozen_clock, tz=ZoneInfo(fixed_tz))

    assert set(intent.services) == {"gcal"}
    tf = intent.entities["timeframe"]
    # frozen_clock = 2024-03-10 (spring-forward). Next Tuesday = 2024-03-12, which
    # lands in EDT (-04:00); asserting the offset is the DST-boundary guard.
    assert tf["start"] == "2024-03-12T00:00:00-04:00"
    assert tf["end"] == "2024-03-12T23:59:59.999999-04:00"


@pytest.mark.asyncio
async def test_classifier_malformed_triggers_single_repair(
    stub_llm, frozen_clock, fixed_tz
):
    """Malformed completion -> exactly one repair reprompt -> valid Intent."""
    stub_llm.sequence("classifier", ["malformed", "malformed_repair"])
    intent = await classify(
        "What's on my calendar next week?", now=frozen_clock, tz=ZoneInfo(fixed_tz)
    )

    assert len(stub_llm.calls) == 2  # initial + exactly one repair
    assert set(intent.services) == {"gcal"}
    assert intent.needs_clarification is False


# --- Tier 2: live classifier, structural invariants only (skipped by default) ---

_LIVE_CASES = (
    ("What's on my calendar next week?", "gcal", False),
    ("Find emails from sarah@company.com about the budget", "gmail", False),
    ("Move the meeting with John", None, True),
)


@pytest.mark.llm
@requires_llm_key
@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "service", "needs_clarification"), _LIVE_CASES)
async def test_classifier_live_contract(
    query, service, needs_clarification, frozen_clock, fixed_tz
):
    intent = await classify(query, now=frozen_clock, tz=ZoneInfo(fixed_tz))
    assert intent.needs_clarification is needs_clarification
    if service is not None:
        assert service in set(intent.services)

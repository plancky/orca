"""Phase-2 Google Workspace provider — STUB ONLY.

Every method raises ``NotImplementedError``; the real OAuth /
``googleapiclient`` wiring lands in Phase 2 (see ``docs/implement_providers.md``).

Hard rule (PLAN.md l.100-103, l.118-120): **no** ``google-auth*`` /
``googleapiclient`` / ``google-genai`` / ``google-generativeai`` import may ever
appear in this file — the F-D scope grep must stay clean and the image CPU-only.
The class exists purely to show where the real client slots in behind the frozen
``Provider`` interface, so ``factory.get_provider`` can return it once
``settings.PROVIDER != "mock"``.
"""

from backend.agents.base import Provider

_PHASE2 = "Phase 2 — see docs/implement_providers.md"


class GoogleProvider(Provider):
    async def search(self, service: str, query: str, filters: dict) -> list[dict]:
        raise NotImplementedError(_PHASE2)

    async def get(self, service: str, item_id: str) -> dict:
        raise NotImplementedError(_PHASE2)

    async def execute(self, service: str, action: str, args: dict) -> dict:
        raise NotImplementedError(_PHASE2)

"""Provider selection — the single DI seam for the data plane.

``get_provider`` returns the concrete ``Provider`` chosen by ``settings.PROVIDER``
(``"mock"`` in Phase 1). Optional ``session`` / ``user_id`` are forwarded to
``MockProvider`` so a tool/executor can bind per-request context in one call;
called bare, ``get_provider()`` is exactly ``MockProvider()`` (PLAN.md l.538-542).
``GoogleProvider`` is the Phase-2 stub and takes no context.
"""

import uuid

from backend.agents.base import Provider
from backend.config import settings
from backend.providers.google.provider import GoogleProvider
from backend.providers.mock.mock_provider import MockProvider


def get_provider(
    session=None, user_id: str | uuid.UUID | None = None
) -> Provider:
    if settings.PROVIDER == "mock":
        return MockProvider(session=session, user_id=user_id)
    return GoogleProvider(session=session, user_id=user_id)

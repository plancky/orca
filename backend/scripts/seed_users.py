"""Standalone first-superuser seed.

Re-exports ``crud.seed_superuser`` so the ``scripts/seed.py`` orchestrator can
import it, and provides a ``main()`` that runs it against a fresh session via
``asyncio.run`` for standalone use (``uv run python -m backend.scripts.seed_users``).
"""

import asyncio

from backend.crud import seed_superuser
from backend.db.session import async_session_factory

__all__ = ["main", "seed_superuser"]


async def _seed() -> None:
    async with async_session_factory() as session:
        await seed_superuser(session)
        await session.commit()


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()

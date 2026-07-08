import asyncio


async def _seed() -> None:
    seeders = []
    try:
        from backend.scripts.seed_users import seed_superuser

        seeders.append(seed_superuser)
    except ImportError:
        print("[seed] superuser seeder absent (Wave B1) — skipping")
    try:
        from backend.providers.mock.seed_corpus import seed_corpus

        seeders.append(seed_corpus)
    except ImportError:
        print("[seed] corpus seeder absent (Wave B4) — skipping")

    if not seeders:
        print("[seed] nothing to seed")
        return

    from backend.db.session import async_session_factory

    async with async_session_factory() as session:
        for seeder in seeders:
            await seeder(session)
        await session.commit()
    print("[seed] done")


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()

import asyncio


async def _seed() -> None:
    print("[seed] user/corpus seeding disabled — nothing to seed")


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()

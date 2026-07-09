import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.db.models import ActionsLog, User
from backend.db.session import async_session_factory


@pytest.mark.asyncio
async def test_jsonb_write_tolerates_uuid_and_datetime():
    the_id = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(email=f"t_{uuid.uuid4().hex[:8]}@t.com", hashed_password="pw")
        session.add(user)
        await session.commit()

        row = ActionsLog(
            user_id=user.id,
            tool="gmail.search_emails",
            args={
                "id": the_id,
                "received_at": datetime(2024, 3, 10, 12, 0, tzinfo=timezone.utc),
            },
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        fetched = (
            await session.execute(select(ActionsLog).where(ActionsLog.id == row.id))
        ).scalar_one()
        assert fetched.args["id"] == str(the_id)

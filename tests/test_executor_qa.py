import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from backend.config import settings
from backend.db.models import Conversation, Task, User
from backend.orchestration.executor import _resolve_args, execute
from backend.orchestration.models.dag import Node, Plan
from backend.orchestration.models.intent import Intent

# Isolated DB for the drop_all/create_all below — never settings.DATABASE_URL:
# running this against the shared dev DB drops every table and wipes all data.
_TEST_DB_NAME = "workspace_orchestrator_executor_test"


@pytest_asyncio.fixture
async def async_session():
    base_url = make_url(settings.DATABASE_URL)
    admin_url = base_url.set(database="postgres")
    test_url = base_url.set(database=_TEST_DB_NAME)

    # CREATE DATABASE cannot run inside a transaction — needs AUTOCOMMIT.
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": _TEST_DB_NAME},
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{_TEST_DB_NAME}"'))
    await admin_engine.dispose()

    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_happy_parallel(async_session):
    # Setup Task
    task_id = str(uuid.uuid4())
    user_id = uuid.uuid4()

    u = User(id=user_id, email="test@test.com", hashed_password="pwd")
    async_session.add(u)
    await async_session.flush()

    conv_id = uuid.uuid4()
    c = Conversation(id=conv_id, user_id=user_id)
    async_session.add(c)
    await async_session.flush()

    t = Task(id=uuid.UUID(task_id), user_id=user_id, conversation_id=conv_id)
    async_session.add(t)
    await async_session.commit()

    plan = Plan(
        nodes=[
            Node(
                id="n1",
                tool="gmail.search_emails",
                args={"query": "hello"},
                depends_on=[],
            ),
            Node(
                id="n2",
                tool="gcal.search_events",
                args={"query": "test"},
                depends_on=[],
            ),
        ]
    )
    intent = Intent(services=["gmail", "gcal"], intent="test", steps=[], entities={})

    # Mock REGISTRY tools for test
    from backend.orchestration.utils.tools import REGISTRY

    async def mock_search_emails(s, u, args):
        return [{"id": "email1"}]

    async def mock_search_events(s, u, args):
        return [{"id": "event1"}]

    REGISTRY["gmail.search_emails"] = mock_search_emails
    REGISTRY["gcal.search_events"] = mock_search_events

    res = await execute(plan, intent, task_id, user_id, async_session)
    assert isinstance(res, dict)
    assert "n1" in res
    assert "n2" in res
    assert res["n1"] == [{"id": "email1"}]
    assert res["n2"] == [{"id": "event1"}]


@pytest.mark.asyncio
async def test_deferred_resolution():
    n2 = Node(id="n2", tool="test", args={"val": "$n1.items.0.id"})
    node_outputs = {"n1": {"items": [{"id": "email123"}]}}
    resolved = await _resolve_args(n2, node_outputs)
    assert resolved["val"] == "email123"

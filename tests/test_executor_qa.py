import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from backend.config import settings
from backend.db.models import ActionsLog, Conversation, Task, User
from backend.orchestration.executor import _resolve_args, execute
from backend.orchestration.models.dag import Node, Plan
from backend.orchestration.models.intent import Intent
from backend.orchestration.utils.checkpoint import Checkpoint, get_checkpoint_for_action


@pytest_asyncio.fixture
async def async_session():
    # Setup test DB
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


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


@pytest.mark.asyncio
async def test_write_gate(async_session):
    task_id = str(uuid.uuid4())
    user_id = uuid.uuid4()

    u = User(id=user_id, email="test2@test.com", hashed_password="pwd")
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
                id="n1", tool="gmail.send_email", args={"to": "a@b.com"}, depends_on=[]
            )
        ]
    )
    intent = Intent(services=["gmail"], intent="test", steps=[], entities={})

    res = await execute(plan, intent, task_id, user_id, async_session)
    assert isinstance(res, Checkpoint)
    assert res.pending_node_id == "n1"
    
    # round-trip check
    cp_dump = res.dump()
    cp_rt = Checkpoint.load(cp_dump)
    assert cp_rt.pending_node_id == "n1"
    
    await async_session.refresh(t)
    assert t.status == "awaiting_confirmation"
    assert t.checkpoint is not None

    from sqlalchemy import select

    log_res = await async_session.execute(
        select(ActionsLog).where(ActionsLog.task_id == uuid.UUID(task_id))
    )
    log = log_res.scalar_one()
    assert log.status == "pending"

    cp = await get_checkpoint_for_action(async_session, log.id)
    assert cp is not None
    assert cp.pending_node_id == "n1"

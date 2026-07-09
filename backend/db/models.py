"""All SQLModel table classes for the orchestrator.

This module is the single, cohesive DB schema — 12 tables plus their
request/response (`*Base`/`*Create`/`*Public`/`*Register`) variants, the auth
`Token` models, and the `get_next_message_seq` helper. It is frozen together
with the Alembic migration in Wave 0 and must import standalone (no live DB).

Table-name conventions (SQLModel lowercases the class name by default):
* ``User`` -> ``user``            ``Task`` -> ``task``
* ``Conversation`` -> ``conversation``   ``Message`` -> ``message``
* ``ActionsLog`` -> ``actions_log``      ``SyncStatus`` -> ``sync_status``
* ``GmailDatasource`` -> ``gmail_datasource`` / ``GmailChunk`` -> ``gmail_vector_store``
* GCal / GDrive mirror Gmail (``gcal_datasource``/``gcal_vector_store``,
  ``gdrive_datasource``/``gdrive_vector_store``).
"""

# allow: SIZE_OK — one cohesive DB schema (12 tables) frozen with the Alembic
# migration; splitting fragments SQLModel.metadata registration + the migration.

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import ARRAY, DateTime, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, Relationship, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Timezone-aware timestamp column type (ER diagram mandates timestamptz).
_TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- #
# Enumerated string values (stored as plain text columns).
# --------------------------------------------------------------------------- #
class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class TaskKind(str, Enum):
    QUERY = "query"
    CONFIRM = "confirm"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCESS = "success"
    FAILED = "failed"


class ActionStatus(str, Enum):
    EXECUTED = "executed"
    PENDING = "pending"
    SIMULATED = "simulated"
    DENIED = "denied"
    FAILED = "failed"


# --------------------------------------------------------------------------- #
# Users + auth tokens.
# --------------------------------------------------------------------------- #
class UserBase(SQLModel):
    email: str = Field(unique=True, index=True)
    full_name: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    timezone: str | None = None


class UserCreate(UserBase):
    password: str


class UserRegister(SQLModel):
    # Open self-registration body — MUST NOT expose is_superuser/is_active
    # (privilege-escalation guard, PLAN.md l.170-171).
    email: str
    password: str
    full_name: str | None = None


class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    google_access_token: str | None = None
    google_refresh_token: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(SQLModel):
    sub: str | None = None


# --------------------------------------------------------------------------- #
# Conversations + messages.
# --------------------------------------------------------------------------- #
class ConversationBase(SQLModel):
    title: str | None = None


class Conversation(ConversationBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    messages: list["Message"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class ConversationPublic(ConversationBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class MessageBase(SQLModel):
    role: str
    content: str


class Message(MessageBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE"
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    task_id: uuid.UUID | None = Field(default=None, foreign_key="task.id")
    seq: int
    # Assistant-only JSONB columns (NULL on role=user rows).
    intent: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    classification: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    entities: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    plan: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    actions_taken: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    pending_actions: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    conversation: Conversation = Relationship(
        back_populates="messages",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class MessagePublic(MessageBase):
    id: uuid.UUID
    conversation_id: uuid.UUID
    task_id: uuid.UUID | None = None
    seq: int
    created_at: datetime
    intent: dict[str, Any] | None = None
    classification: dict[str, Any] | None = None
    entities: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    actions_taken: dict[str, Any] | None = None
    pending_actions: dict[str, Any] | None = None


class ConversationWithMessages(ConversationPublic):
    messages: list[MessagePublic] = []


# --------------------------------------------------------------------------- #
# Tasks (lifecycle SoR).
# --------------------------------------------------------------------------- #
class Task(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id", nullable=False, ondelete="CASCADE"
    )
    kind: str = TaskKind.QUERY.value
    status: str = TaskStatus.QUEUED.value
    progress: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    checkpoint: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    result: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    error: str | None = None
    parent_task_id: uuid.UUID | None = Field(default=None, foreign_key="task.id")
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class TaskPublic(SQLModel):
    id: uuid.UUID
    kind: str
    status: str
    conversation_id: uuid.UUID
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    parent_task_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Gmail datasource + chunks (gmail_vector_store).
# --------------------------------------------------------------------------- #
class GmailDatasource(SQLModel, table=True):
    __tablename__ = "gmail_datasource"
    __table_args__ = (UniqueConstraint("user_id", "email_id"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    email_id: str
    thread_id: str | None = None
    sender_email_id: str | None = None
    receiver_email_id: str | None = None
    subject: str | None = None
    content: str
    labels: list[str] = Field(default_factory=list, sa_type=ARRAY(String))
    sent_at: datetime | None = Field(default=None, sa_type=_TS)
    received_at: datetime | None = Field(default=None, sa_type=_TS)
    chunks: list["GmailChunk"] = Relationship(
        back_populates="datasource",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class GmailChunk(SQLModel, table=True):
    __tablename__ = "gmail_vector_store"
    __table_args__ = (UniqueConstraint("datasource_id", "chunk_index"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    datasource_id: uuid.UUID = Field(
        foreign_key="gmail_datasource.id", nullable=False, ondelete="CASCADE"
    )
    user_id: uuid.UUID  # denormalized for no-join user prefilter
    thread_id: str | None = None  # denormalized for thread reconstruction
    chunk_index: int
    chunk_text: str
    token_count: int = 0
    embedding: list[float] | None = Field(default=None, sa_type=VECTOR(1024))
    datasource: GmailDatasource = Relationship(
        back_populates="chunks",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


# --------------------------------------------------------------------------- #
# GCal datasource + chunks (gcal_vector_store).
# --------------------------------------------------------------------------- #
class GCalDatasource(SQLModel, table=True):
    __tablename__ = "gcal_datasource"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    event_id: str
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_at: datetime | None = Field(default=None, sa_type=_TS)
    end_at: datetime | None = Field(default=None, sa_type=_TS)
    attendees: list[str] = Field(default_factory=list, sa_type=ARRAY(String))
    chunks: list["GCalChunk"] = Relationship(
        back_populates="datasource",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class GCalChunk(SQLModel, table=True):
    __tablename__ = "gcal_vector_store"
    __table_args__ = (UniqueConstraint("datasource_id", "chunk_index"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    datasource_id: uuid.UUID = Field(
        foreign_key="gcal_datasource.id", nullable=False, ondelete="CASCADE"
    )
    user_id: uuid.UUID  # denormalized
    chunk_index: int
    chunk_text: str
    token_count: int = 0
    embedding: list[float] | None = Field(default=None, sa_type=VECTOR(1024))
    datasource: GCalDatasource = Relationship(
        back_populates="chunks",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


# --------------------------------------------------------------------------- #
# GDrive datasource + chunks (gdrive_vector_store).
# --------------------------------------------------------------------------- #
class GDriveDatasource(SQLModel, table=True):
    __tablename__ = "gdrive_datasource"
    __table_args__ = (UniqueConstraint("user_id", "file_id"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    file_id: str
    name: str | None = None
    mime_type: str | None = None
    content: str
    owner: str | None = None
    modified_at: datetime | None = Field(default=None, sa_type=_TS)
    chunks: list["GDriveChunk"] = Relationship(
        back_populates="datasource",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


class GDriveChunk(SQLModel, table=True):
    __tablename__ = "gdrive_vector_store"
    __table_args__ = (UniqueConstraint("datasource_id", "chunk_index"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    datasource_id: uuid.UUID = Field(
        foreign_key="gdrive_datasource.id", nullable=False, ondelete="CASCADE"
    )
    user_id: uuid.UUID  # denormalized
    chunk_index: int
    chunk_text: str
    token_count: int = 0
    embedding: list[float] | None = Field(default=None, sa_type=VECTOR(1024))
    datasource: GDriveDatasource = Relationship(
        back_populates="chunks",
        sa_relationship_kwargs={"lazy": "selectin"},
    )


# --------------------------------------------------------------------------- #
# Actions log (audit + write-confirmation gate) and per-user sync status.
# --------------------------------------------------------------------------- #
class ActionsLog(SQLModel, table=True):
    __tablename__ = "actions_log"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversation.id"
    )
    task_id: uuid.UUID | None = Field(default=None, foreign_key="task.id")
    tool: str
    args: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    status: str = ActionStatus.PENDING.value
    result: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class SyncStatus(SQLModel, table=True):
    __tablename__ = "sync_status"
    __table_args__ = (UniqueConstraint("user_id", "service"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    service: str = Field(nullable=False)
    last_synced_at: datetime | None = Field(default=None, sa_type=_TS)
    item_count: int = 0
    cursor: str | None = None


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
async def get_next_message_seq(
    session: AsyncSession, conversation_id: uuid.UUID
) -> int:
    """Return the next ``seq`` for a conversation.

    Locks the parent Conversation row (FOR UPDATE) to serialize concurrent
    turns, then computes MAX(seq)+1 — Postgres forbids FOR UPDATE on an
    aggregate select, so the lock is taken on the Conversation row instead.
    """
    await session.execute(
        select(Conversation.id)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    result = await session.execute(
        select(func.coalesce(func.max(Message.seq), -1) + 1).where(
            Message.conversation_id == conversation_id
        )
    )
    return result.scalar_one()

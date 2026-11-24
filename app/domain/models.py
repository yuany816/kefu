from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationState(StrEnum):
    AI_ACTIVE = "ai_active"
    AI_THINKING = "ai_thinking"
    HUMAN_PENDING = "human_pending"
    HUMAN_ACTIVE = "human_active"
    COOLDOWN = "cooldown"
    CLOSED = "closed"


class MessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    FILE = "file"
    SYSTEM = "system"


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TenantOut(BaseModel):
    id: UUID
    name: str
    status: str
    created_at: datetime


class TextDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    source_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentOut(BaseModel):
    id: UUID
    title: str
    status: str
    chunk_count: int
    checksum: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)


class SearchHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    title: str
    section_title: str | None = None
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    device_id: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=200)
    external_event_id: str = Field(min_length=1, max_length=300)
    conversation_external_id: str = Field(min_length=1, max_length=300)
    sender_external_id: str | None = Field(default=None, max_length=300)
    message_type: MessageType
    content: str = Field(default="", max_length=20000)
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventReceipt(BaseModel):
    accepted: bool
    duplicate: bool = False
    conversation_id: UUID | None = None
    state: ConversationState | None = None
    action: str | None = None


class AgentCommand(BaseModel):
    id: UUID
    conversation_id: UUID
    command_type: str
    payload: dict[str, Any]
    idempotency_key: str
    created_at: datetime


class CommandAck(BaseModel):
    success: bool
    external_message_id: str | None = None
    error: str | None = Field(default=None, max_length=2000)


class TakeoverRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="manual_takeover", max_length=500)


class ReleaseRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=200)


class ConversationOut(BaseModel):
    id: UUID
    account_external_id: str
    conversation_external_id: str
    state: ConversationState
    handoff_reason: str | None
    ai_message_count: int
    updated_at: datetime


class WeComStatusOut(BaseModel):
    configured: bool
    token_valid: bool
    corp_id: str


class WeComGroupListRequest(BaseModel):
    status_filter: int = Field(default=0, ge=0, le=4)
    owner_user_ids: list[str] = Field(default_factory=list, max_length=100)
    cursor: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=100, ge=1, le=1000)


class WeComArchiveBatch(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class WeComArchiveBatchResult(BaseModel):
    received: int
    accepted: int
    skipped: int
    duplicate: int
    handoff: int


from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config import Settings
from app.domain.models import AgentEvent, ConversationState, MessageType, SearchHit
from app.services.orchestrator import CustomerServiceOrchestrator


class FakeRepository:
    def __init__(self) -> None:
        self.conversation_id = uuid4()
        self.state = ConversationState.AI_ACTIVE
        self.commands: list[dict] = []
        self.handoffs: list[str] = []
        self.events: set[str] = set()

    def upsert_device(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def ingest_inbound_message(self, **kwargs):  # noqa: ANN003, ANN201
        duplicate = kwargs["external_event_id"] in self.events
        self.events.add(kwargs["external_event_id"])
        return {
            "id": self.conversation_id,
            "state": self.state.value,
            "ai_message_count": 0,
        }, duplicate

    def transition_conversation(self, *, target, **kwargs):  # noqa: ANN003, ANN201
        self.state = target
        return {"id": self.conversation_id, "state": target.value}

    def get_recent_history(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return ["客户：签收后可以退货吗？"]

    def request_handoff(self, *, reason, **kwargs):  # noqa: ANN003, ANN201
        self.state = ConversationState.HUMAN_PENDING
        self.handoffs.append(reason)
        return {"reason": reason}

    def enqueue_command(self, **kwargs):  # noqa: ANN003, ANN201
        self.commands.append(kwargs)
        return kwargs


class FakeKnowledge:
    def __init__(self, score: float = 0.8) -> None:
        self.score = score

    async def search(self, **kwargs):  # noqa: ANN003, ANN201
        if self.score == 0:
            return []
        return [
            SearchHit(
                chunk_id=uuid4(),
                document_id=uuid4(),
                title="退货政策",
                content="签收后7天内，不影响二次销售可申请退货。",
                score=self.score,
            )
        ]


class FakeChat:
    async def answer(self, **kwargs):  # noqa: ANN003, ANN201
        return "签收后7天内，不影响二次销售可申请退货。"


def make_event(content: str, event_id: str = "event-1") -> AgentEvent:
    return AgentEvent(
        device_id="device-1",
        account_id="account-1",
        external_event_id=event_id,
        conversation_external_id="customer-1",
        sender_external_id="customer-1",
        message_type=MessageType.TEXT,
        content=content,
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_grounded_reply_is_enqueued() -> None:
    repository = FakeRepository()
    orchestrator = CustomerServiceOrchestrator(
        repository=repository,
        knowledge=FakeKnowledge(),
        chat_provider=FakeChat(),
        settings=Settings(),
    )

    receipt = await orchestrator.handle_event(
        tenant_id=uuid4(), event=make_event("签收后可以退货吗？")
    )

    assert receipt.action == "reply_enqueued"
    assert repository.commands[0]["payload"]["content"].startswith("【AI客服】")
    assert repository.commands[0]["payload"]["expected_last_event_id"] == "event-1"


@pytest.mark.asyncio
async def test_customer_can_request_human() -> None:
    repository = FakeRepository()
    orchestrator = CustomerServiceOrchestrator(
        repository=repository,
        knowledge=FakeKnowledge(),
        chat_provider=FakeChat(),
        settings=Settings(),
    )

    receipt = await orchestrator.handle_event(
        tenant_id=uuid4(), event=make_event("你说得不对，转人工")
    )

    assert receipt.state == ConversationState.HUMAN_PENDING
    assert repository.handoffs == ["customer_requested_human"]
    assert repository.commands[0]["payload"]["handoff"] is True


@pytest.mark.asyncio
async def test_duplicate_message_does_not_enqueue_twice() -> None:
    repository = FakeRepository()
    orchestrator = CustomerServiceOrchestrator(
        repository=repository,
        knowledge=FakeKnowledge(),
        chat_provider=FakeChat(),
        settings=Settings(),
    )
    event = make_event("签收后可以退货吗？")

    await orchestrator.handle_event(tenant_id=uuid4(), event=event)
    second = await orchestrator.handle_event(tenant_id=uuid4(), event=event)

    assert second.duplicate is True
    assert len(repository.commands) == 1


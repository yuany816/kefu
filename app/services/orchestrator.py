import re
from uuid import UUID

from app.config import Settings
from app.domain.models import AgentEvent, ConversationState, EventReceipt
from app.infra.repository import Repository
from app.services.ai import ChatProvider
from app.services.knowledge import KnowledgeService


class CustomerServiceOrchestrator:
    handoff_pattern = re.compile(
        r"(人工|真人|转人工|投诉|你.{0,4}(回答|说).{0,6}(不对|错误)|答非所问)",
        re.IGNORECASE,
    )
    high_risk_pattern = re.compile(
        r"(赔偿|起诉|律师|报警|人身伤害|过敏|中毒|诈骗|退款失败|账户被盗)",
        re.IGNORECASE,
    )
    uncertain_answer_pattern = re.compile(r"(无法确认|没有足够信息|建议.{0,4}人工|转接人工)")

    def __init__(
        self,
        *,
        repository: Repository,
        knowledge: KnowledgeService,
        chat_provider: ChatProvider,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.knowledge = knowledge
        self.chat_provider = chat_provider
        self.settings = settings

    async def handle_event(self, *, tenant_id: UUID, event: AgentEvent) -> EventReceipt:
        self.repository.upsert_device(
            tenant_id,
            event.device_id,
            platform=str(event.metadata.get("platform", "unknown")),
        )
        conversation, duplicate = self.repository.ingest_inbound_message(
            tenant_id=tenant_id,
            device_id=event.device_id,
            account_id=event.account_id,
            external_event_id=event.external_event_id,
            conversation_external_id=event.conversation_external_id,
            sender_external_id=event.sender_external_id,
            message_type=event.message_type.value,
            content=event.content,
            occurred_at=event.occurred_at,
            metadata=event.metadata,
        )
        conversation_id = conversation["id"]
        state = ConversationState(conversation["state"])
        if duplicate:
            return EventReceipt(
                accepted=True,
                duplicate=True,
                conversation_id=conversation_id,
                state=state,
                action="ignored_duplicate",
            )
        if state in {ConversationState.HUMAN_PENDING, ConversationState.HUMAN_ACTIVE}:
            return EventReceipt(
                accepted=True,
                conversation_id=conversation_id,
                state=state,
                action="human_owns_conversation",
            )

        if event.message_type.value != "text":
            return self._handoff(
                tenant_id=tenant_id,
                event=event,
                conversation_id=conversation_id,
                reason=f"unsupported_message_type:{event.message_type.value}",
            )
        if self.handoff_pattern.search(event.content):
            return self._handoff(
                tenant_id=tenant_id,
                event=event,
                conversation_id=conversation_id,
                reason="customer_requested_human",
            )
        if self.high_risk_pattern.search(event.content):
            return self._handoff(
                tenant_id=tenant_id,
                event=event,
                conversation_id=conversation_id,
                reason="high_risk_intent",
            )

        self.repository.transition_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            target=ConversationState.AI_THINKING,
        )
        hits = await self.knowledge.search(tenant_id=tenant_id, query=event.content, limit=8)
        best_score = hits[0].score if hits else 0.0
        if not hits or best_score < self.settings.min_retrieval_score:
            return self._handoff(
                tenant_id=tenant_id,
                event=event,
                conversation_id=conversation_id,
                reason="insufficient_knowledge",
            )

        history = self.repository.get_recent_history(tenant_id, conversation_id)
        answer = await self.chat_provider.answer(
            question=event.content,
            contexts=[hit.content for hit in hits],
            history=history,
        )
        if self.uncertain_answer_pattern.search(answer):
            return self._handoff(
                tenant_id=tenant_id,
                event=event,
                conversation_id=conversation_id,
                reason="model_uncertain",
            )

        disclosure = "您好，我是本店AI客服助手。" if conversation["ai_message_count"] == 0 else ""
        content = f"{self.settings.auto_reply_prefix}{disclosure}{answer}"
        self.repository.enqueue_command(
            tenant_id=tenant_id,
            device_id=event.device_id,
            conversation_id=conversation_id,
            command_type="send_message",
            payload={
                "conversation_external_id": event.conversation_external_id,
                "expected_last_event_id": event.external_event_id,
                "content": content,
                "confidence": best_score,
                "ai_generated": True,
            },
            idempotency_key=f"reply:{event.external_event_id}",
        )
        return EventReceipt(
            accepted=True,
            conversation_id=conversation_id,
            state=ConversationState.AI_THINKING,
            action="reply_enqueued",
        )

    def _handoff(
        self,
        *,
        tenant_id: UUID,
        event: AgentEvent,
        conversation_id: UUID,
        reason: str,
    ) -> EventReceipt:
        self.repository.request_handoff(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
        )
        content = (
            f"{self.settings.auto_reply_prefix}抱歉，我暂时无法准确处理这个问题，"
            "正在为您转接人工客服，请稍候。"
        )
        self.repository.enqueue_command(
            tenant_id=tenant_id,
            device_id=event.device_id,
            conversation_id=conversation_id,
            command_type="send_message",
            payload={
                "conversation_external_id": event.conversation_external_id,
                "expected_last_event_id": event.external_event_id,
                "content": content,
                "confidence": 0.0,
                "ai_generated": True,
                "handoff": True,
                "handoff_reason": reason,
            },
            idempotency_key=f"handoff:{event.external_event_id}",
        )
        return EventReceipt(
            accepted=True,
            conversation_id=conversation_id,
            state=ConversationState.HUMAN_PENDING,
            action=f"handoff:{reason}",
        )


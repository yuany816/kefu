import pytest

from app.domain.models import ConversationState
from app.domain.state_machine import InvalidStateTransition, ensure_transition


def test_human_takeover_path_is_valid() -> None:
    ensure_transition(ConversationState.AI_ACTIVE, ConversationState.HUMAN_ACTIVE)
    ensure_transition(ConversationState.HUMAN_ACTIVE, ConversationState.COOLDOWN)
    ensure_transition(ConversationState.COOLDOWN, ConversationState.AI_ACTIVE)


def test_ai_cannot_bypass_human_ownership() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(ConversationState.HUMAN_ACTIVE, ConversationState.AI_ACTIVE)


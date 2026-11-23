from app.domain.models import ConversationState


ALLOWED_TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    ConversationState.AI_ACTIVE: {
        ConversationState.AI_THINKING,
        ConversationState.HUMAN_PENDING,
        ConversationState.HUMAN_ACTIVE,
        ConversationState.CLOSED,
    },
    ConversationState.AI_THINKING: {
        ConversationState.AI_ACTIVE,
        ConversationState.HUMAN_PENDING,
        ConversationState.HUMAN_ACTIVE,
        ConversationState.CLOSED,
    },
    ConversationState.HUMAN_PENDING: {
        ConversationState.HUMAN_ACTIVE,
        ConversationState.AI_ACTIVE,
        ConversationState.CLOSED,
    },
    ConversationState.HUMAN_ACTIVE: {
        ConversationState.COOLDOWN,
        ConversationState.CLOSED,
    },
    ConversationState.COOLDOWN: {
        ConversationState.AI_ACTIVE,
        ConversationState.HUMAN_ACTIVE,
        ConversationState.CLOSED,
    },
    ConversationState.CLOSED: {ConversationState.AI_ACTIVE},
}


class InvalidStateTransition(ValueError):
    pass


def ensure_transition(current: ConversationState, target: ConversationState) -> None:
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f"invalid conversation transition: {current} -> {target}")


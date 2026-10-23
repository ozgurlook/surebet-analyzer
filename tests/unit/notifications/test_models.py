from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from surebet.domain.enums import MarketType
from surebet.notifications.models import (
    LifecycleStatus,
    OutboxActionDraft,
    OutboxActionType,
    card_key,
)


def test_card_key_joins_matched_id_and_market() -> None:
    assert card_key("event-1", MarketType.TOTAL_GOALS_25) == "event-1:OU25"


def test_lifecycle_statuses_are_exactly_the_designed_set() -> None:
    assert {status.value for status in LifecycleStatus} == {
        "new",
        "active",
        "watch",
        "stale",
        "closed",
    }


def test_outbox_action_types_are_exactly_the_designed_set() -> None:
    assert {action.value for action in OutboxActionType} == {
        "send_card",
        "edit_card",
        "send_alert",
        "send_revived",
        "send_closed",
    }


def test_outbox_draft_defaults_to_no_explicit_message_and_audible_delivery() -> None:
    draft = OutboxActionDraft(
        action=OutboxActionType.SEND_CARD,
        text="x",
        fingerprint="f",
        next_attempt_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    assert draft.message_id is None
    assert draft.silent is False


def test_outbox_draft_is_frozen() -> None:
    draft = OutboxActionDraft(
        action=OutboxActionType.SEND_CARD,
        text="x",
        fingerprint="f",
        next_attempt_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        draft.text = "y"

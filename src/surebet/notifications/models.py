"""Value types shared across the Telegram notification subsystem."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from surebet.domain.enums import MarketType, Source
from surebet.opportunities.strategy import CalculatedOpportunity


def card_key(matched_id: str, market_type: MarketType) -> str:
    """Identity of one Telegram card: one match, one market."""
    return f"{matched_id}:{market_type.value}"


class DualPolicyEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    matched_id: str
    market_type: MarketType
    selected: CalculatedOpportunity
    draw_only: CalculatedOpportunity
    uses_source_a_match_sides: bool
    relevant_sources: frozenset[Source]

    @property
    def card_key(self) -> str:
        return card_key(self.matched_id, self.market_type)


class LifecycleStatus(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    WATCH = "watch"
    STALE = "stale"
    CLOSED = "closed"


class OutboxActionType(StrEnum):
    SEND_CARD = "send_card"
    EDIT_CARD = "edit_card"
    SEND_ALERT = "send_alert"
    SEND_REVIVED = "send_revived"
    SEND_CLOSED = "send_closed"


class CardState(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    chat_id: int
    card_key: str
    matched_id: str
    market_type: MarketType
    generation: int
    status: LifecycleStatus
    message_id: int | None
    evaluation: DualPolicyEvaluation

    first_profit_bps: int
    current_profit_bps: int
    peak_profit_bps: int

    anchor_bps: int
    last_alert_at: datetime | None
    below_since: datetime | None
    closed_at: datetime | None
    revival_count: int

    first_seen_at: datetime
    last_seen_at: datetime
    last_edit_at: datetime | None
    rendered_fingerprint: str | None


class OutboxActionDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: OutboxActionType
    text: str
    fingerprint: str
    next_attempt_at: datetime
    message_id: int | None = None
    silent: bool = False
    reply_markup: dict[str, Any] | None = None


class OutboxRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    outbox_id: int
    user_id: int
    chat_id: int
    card_key: str
    matched_id: str
    generation: int
    action: OutboxActionType
    text: str
    fingerprint: str
    message_id: int | None = None
    silent: bool = False
    attempt_count: int
    next_attempt_at: datetime
    reply_markup: dict[str, Any] | None = None


class LifecycleDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: CardState
    requested_actions: tuple[OutboxActionType, ...]


class TelegramFailureKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    MESSAGE_NOT_FOUND = "message_not_found"
    CHAT_UNREACHABLE = "chat_unreachable"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class TelegramResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    message_id: int | None = None
    failure_kind: TelegramFailureKind | None = None
    retry_after_seconds: int | None = None
    safe_error: str | None = None

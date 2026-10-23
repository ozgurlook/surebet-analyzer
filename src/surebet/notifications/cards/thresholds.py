"""Pure arithmetic behind the card lifecycle: profit, thresholds, anchors."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from surebet.opportunities.strategy import CalculatedOpportunity

CLOSE_FACTOR = 0.80
CLOSE_GRACE_SECONDS = 180
REVIVAL_WINDOW_SECONDS = 900
MIN_EDIT_INTERVAL_SECONDS = 60
# How long a closed card is kept before it is deleted outright. Far longer than
# REVIVAL_WINDOW_SECONDS, so nothing that could still act is ever removed.
CARD_RETENTION_SECONDS = 7 * 24 * 60 * 60
OUTBOX_RETENTION_SECONDS = 7 * 24 * 60 * 60

__all__ = [
    "CARD_RETENTION_SECONDS",
    "CLOSE_FACTOR",
    "CLOSE_GRACE_SECONDS",
    "MIN_EDIT_INTERVAL_SECONDS",
    "OUTBOX_RETENTION_SECONDS",
    "REVIVAL_WINDOW_SECONDS",
    "alert_threshold_bps",
    "close_floor_bps",
    "meets_threshold",
    "profit_basis_points",
    "should_alert",
]


def profit_basis_points(opportunity: CalculatedOpportunity) -> int:
    value = (
        Decimal(opportunity.min_adjusted_profit_cents)
        * Decimal(10_000)
        / Decimal(opportunity.budget_cents)
    )
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def meets_threshold(opportunity: CalculatedOpportunity, threshold_bps: int) -> bool:
    return opportunity.min_adjusted_profit_cents * 10_000 >= (
        opportunity.budget_cents * threshold_bps
    )


def close_floor_bps(minimum_profit_bps: int) -> int:
    """Profit must fall below this, not merely below the threshold, to close a card."""
    return int(
        (Decimal(minimum_profit_bps) * Decimal(str(CLOSE_FACTOR))).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )


def alert_threshold_bps(
    delta_bps: int, *, decrease_threshold_bps: int, increase_threshold_bps: int
) -> int:
    return decrease_threshold_bps if delta_bps < 0 else increase_threshold_bps


def should_alert(
    *,
    current_bps: int,
    anchor_bps: int,
    decrease_threshold_bps: int,
    increase_threshold_bps: int,
    last_alert_at: datetime | None,
    cooldown_seconds: int,
    now: datetime,
) -> bool:
    """One anchor, one delta -- so a decrease and an increase can never both fire."""
    delta = current_bps - anchor_bps
    if delta == 0:
        return False
    threshold = alert_threshold_bps(
        delta,
        decrease_threshold_bps=decrease_threshold_bps,
        increase_threshold_bps=increase_threshold_bps,
    )
    if abs(delta) < threshold:
        return False
    return last_alert_at is None or now - last_alert_at >= timedelta(seconds=cooldown_seconds)

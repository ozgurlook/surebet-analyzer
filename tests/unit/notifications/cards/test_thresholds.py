from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from surebet.notifications.cards.thresholds import (
    CLOSE_FACTOR,
    CLOSE_GRACE_SECONDS,
    MIN_EDIT_INTERVAL_SECONDS,
    REVIVAL_WINDOW_SECONDS,
    alert_threshold_bps,
    close_floor_bps,
    meets_threshold,
    profit_basis_points,
    should_alert,
)
from surebet.opportunities.strategy import CalculatedOpportunity

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _sample_opportunity(*, budget_cents: int, profit_cents: int) -> CalculatedOpportunity:
    return CalculatedOpportunity.model_construct(
        opportunity_id="event-1:source_c:source_a:source_b",
        matched_id="event-1",
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=NOW,
        budget_cents=budget_cents,
        include_source_a_match_sides=False,
        selections=MappingProxyType({}),
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_cents,
        min_adjusted_profit_percent=round(profit_cents / budget_cents * 100, 2),
        min_standard_profit_cents=profit_cents,
        min_standard_profit_percent=round(profit_cents / budget_cents * 100, 2),
        is_positive=profit_cents > 0,
        oldest_quote_age_seconds=10,
        calculated_at=NOW,
    )


def test_constants() -> None:
    assert CLOSE_FACTOR == 0.80
    assert CLOSE_GRACE_SECONDS == 180
    assert REVIVAL_WINDOW_SECONDS == 900
    assert MIN_EDIT_INTERVAL_SECONDS == 60


def test_close_floor_is_a_fixed_fraction_of_the_user_threshold() -> None:
    assert CLOSE_FACTOR == 0.80
    assert close_floor_bps(100) == 80
    assert close_floor_bps(95) == 76


def test_alert_threshold_bps() -> None:
    assert alert_threshold_bps(-15, decrease_threshold_bps=20, increase_threshold_bps=30) == 20
    assert alert_threshold_bps(15, decrease_threshold_bps=20, increase_threshold_bps=30) == 30
    assert alert_threshold_bps(0, decrease_threshold_bps=20, increase_threshold_bps=30) == 30


def test_profit_basis_points_and_meets_threshold() -> None:
    opp_9499 = _sample_opportunity(budget_cents=1_000_000, profit_cents=9_499)
    assert profit_basis_points(opp_9499) == 95
    assert meets_threshold(opp_9499, 95) is False

    opp_9500 = _sample_opportunity(budget_cents=1_000_000, profit_cents=9_500)
    assert profit_basis_points(opp_9500) == 95
    assert meets_threshold(opp_9500, 95) is True


@pytest.mark.parametrize(
    ("current", "anchor", "expected"),
    [
        (215, 240, True),  # 0,25 puan düşüş, eşik 0,20
        (225, 240, False),  # 0,15 puan düşüş, eşiğin altında
        (265, 240, True),  # 0,25 puan yükseliş
        (255, 240, False),  # 0,15 puan yükseliş
        (240, 240, False),  # sapma yok
    ],
)
def test_should_alert_respects_directional_thresholds(
    current: int, anchor: int, expected: bool
) -> None:
    assert (
        should_alert(
            current_bps=current,
            anchor_bps=anchor,
            decrease_threshold_bps=20,
            increase_threshold_bps=20,
            last_alert_at=None,
            cooldown_seconds=300,
            now=NOW,
        )
        is expected
    )


def test_should_alert_is_blocked_inside_the_cooldown() -> None:
    assert (
        should_alert(
            current_bps=100,
            anchor_bps=240,
            decrease_threshold_bps=20,
            increase_threshold_bps=20,
            last_alert_at=NOW - timedelta(seconds=299),
            cooldown_seconds=300,
            now=NOW,
        )
        is False
    )


def test_should_alert_fires_exactly_at_the_cooldown_boundary() -> None:
    assert (
        should_alert(
            current_bps=100,
            anchor_bps=240,
            decrease_threshold_bps=20,
            increase_threshold_bps=20,
            last_alert_at=NOW - timedelta(seconds=300),
            cooldown_seconds=300,
            now=NOW,
        )
        is True
    )

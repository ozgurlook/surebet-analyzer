from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from surebet.arbitrage.verify import verify_allocation
from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Allocation, Quote

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _quotes(*, event_id: str = "event") -> dict[Outcome, Quote]:
    odds = {
        Outcome.HOME: "2.90",
        Outcome.DRAW: "3.60",
        Outcome.AWAY: "3.10",
    }
    return {
        outcome: Quote(
            source=Source.SOURCE_B,
            event_id=event_id,
            outcome=outcome,
            odds=value,
            observed_at=NOW,
        )
        for outcome, value in odds.items()
    }


def test_verify_allocation_returns_exact_scenario_order_and_zero_bonus() -> None:
    allocation = Allocation(stakes_cents={outcome: 10_000 for outcome in Outcome})
    result = verify_allocation(_quotes(), allocation)
    assert tuple(item.outcome for item in result) == tuple(Outcome)
    assert all(item.bonus_delta_cents == 0 for item in result)
    assert all(item.adjusted_profit_cents == item.standard_profit_cents for item in result)
    assert [item.gross_return_cents for item in result] == [29_000, 36_000, 31_000]
    assert [item.standard_profit_cents for item in result] == [-1_000, 6_000, 1_000]


def test_verify_rejects_false_arb_created_by_rounding() -> None:
    quotes = {
        Outcome.HOME: Quote(
            source=Source.SOURCE_A, event_id="e", outcome=Outcome.HOME, odds="2.01", observed_at=NOW
        ),
        Outcome.DRAW: Quote(
            source=Source.SOURCE_B, event_id="e", outcome=Outcome.DRAW, odds="3.01", observed_at=NOW
        ),
        Outcome.AWAY: Quote(
            source=Source.SOURCE_C, event_id="e", outcome=Outcome.AWAY, odds="3.01", observed_at=NOW
        ),
    }
    result = verify_allocation(
        quotes, Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    )
    assert min(item.standard_profit_cents for item in result) < 0


@pytest.mark.parametrize(
    "quotes",
    [
        {Outcome.HOME: _quotes()[Outcome.HOME], Outcome.DRAW: _quotes()[Outcome.DRAW]},
        {**_quotes(), "OTHER": _quotes()[Outcome.HOME]},
        {**_quotes(), Outcome.HOME: _quotes()[Outcome.DRAW]},
    ],
)
def test_verify_rejects_quote_map_contradictions(quotes: object) -> None:
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    with pytest.raises((TypeError, ValueError, ValidationError)):
        verify_allocation(quotes, allocation)  # type: ignore[arg-type]


def test_verify_rejects_quotes_from_different_events() -> None:
    quotes = _quotes()
    quotes[Outcome.AWAY] = quotes[Outcome.AWAY].model_copy(update={"event_id": "other"})
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    with pytest.raises(ValueError):
        verify_allocation(quotes, allocation)


def test_verify_uses_conservative_payout_rounding() -> None:
    quotes = _quotes()
    quotes[Outcome.HOME] = quotes[Outcome.HOME].model_copy(update={"odds": Decimal("1.3333")})
    result = verify_allocation(
        quotes, Allocation(stakes_cents={Outcome.HOME: 1001, Outcome.DRAW: 0, Outcome.AWAY: 0})
    )
    assert result[0].gross_return_cents == 1_334


def test_verify_handles_stakes_larger_than_decimal_context_precision() -> None:
    stake = 10**100 + 1
    result = verify_allocation(
        _quotes(),
        Allocation(stakes_cents={Outcome.HOME: stake, Outcome.DRAW: 0, Outcome.AWAY: 0}),
    )
    assert result[0].gross_return_cents == stake * 29 // 10


def test_verify_rejects_quote_odds_with_more_than_four_fractional_digits() -> None:
    with pytest.raises(ValidationError):
        Quote(
            source=Source.SOURCE_B,
            event_id="event",
            outcome=Outcome.HOME,
            odds="2.90001",
            observed_at=NOW,
        )

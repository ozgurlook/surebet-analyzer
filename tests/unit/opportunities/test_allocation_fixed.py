from decimal import Decimal

from surebet.domain.enums import Outcome, Source, TwoWayOutcome
from surebet.opportunities.allocation_fixed import (
    calculate_budget_allocation,
    calculate_fixed_stake_allocation,
)


def test_fixed_stake_3way_basic():
    odds = {
        Outcome.HOME: Decimal("2.0"),
        Outcome.DRAW: Decimal("4.0"),
        Outcome.AWAY: Decimal("4.0"),
    }
    sources = {
        Outcome.HOME: Source.SOURCE_A,
        Outcome.DRAW: Source.SOURCE_A,
        Outcome.AWAY: Source.SOURCE_A,
    }
    # Fix HOME to 1000 TL (100_000 cents)
    # Target payout: 100_000 * 2.0 = 200_000 cents
    # DRAW stake should be 200_000 / 4.0 = 50_000 cents (500 TL)
    # AWAY stake should be 200_000 / 4.0 = 50_000 cents (500 TL)
    result = calculate_fixed_stake_allocation(
        fixed_outcome=Outcome.HOME,
        fixed_stake_cents=100_000,
        odds=odds,
        sources=sources,
    )
    assert result.is_fixed_stake is True
    assert result.fixed_outcome == Outcome.HOME
    assert result.total_budget_cents == 200_000  # 1000 + 500 + 500 = 2000 TL
    stakes = {line.outcome: line.stake_cents for line in result.lines}
    assert stakes[Outcome.HOME] == 100_000
    assert stakes[Outcome.DRAW] == 50_000
    assert stakes[Outcome.AWAY] == 50_000


def test_fixed_stake_3way_cashback_equalized():
    # Rizespor - Alanyaspor real opportunity
    odds = {
        Outcome.HOME: Decimal("1.8771"),
        Outcome.DRAW: Decimal("3.50"),
        Outcome.AWAY: Decimal("4.90"),
    }
    sources = {
        Outcome.HOME: Source.SOURCE_B,
        Outcome.DRAW: Source.SOURCE_B,
        Outcome.AWAY: Source.SOURCE_A,
    }
    result = calculate_fixed_stake_allocation(
        fixed_outcome=Outcome.HOME,
        fixed_stake_cents=350_000,  # 3500 TL
        odds=odds,
        sources=sources,
    )
    stakes = {line.outcome: line.stake_cents for line in result.lines}
    assert stakes[Outcome.HOME] == 350_000
    payouts = [line.payout_cents for line in result.lines]
    # Payouts must be equalized across all 3 scenarios within 2 cents
    assert max(payouts) - min(payouts) <= 2
    # Equal profit of ~154 TL (over 2% profit)
    assert result.profit_bps >= 230


def test_fixed_stake_2way():
    odds = {
        TwoWayOutcome.OVER: Decimal("2.10"),
        TwoWayOutcome.UNDER: Decimal("2.10"),
    }
    sources = {
        TwoWayOutcome.OVER: Source.SOURCE_B,
        TwoWayOutcome.UNDER: Source.SOURCE_A,
    }
    result = calculate_fixed_stake_allocation(
        fixed_outcome=TwoWayOutcome.OVER,
        fixed_stake_cents=210_000,  # 2100 TL
        odds=odds,
        sources=sources,
    )
    stakes = {line.outcome: line.stake_cents for line in result.lines}
    assert stakes[TwoWayOutcome.OVER] == 210_000
    # Because OVER is on SOURCE_B (10% cashback), UNDER requires 200_000 cents for equal 4410 TL payout
    assert stakes[TwoWayOutcome.UNDER] == 200_000
    payouts = [line.payout_cents for line in result.lines]
    assert payouts[0] == payouts[1] == 441_000
    assert result.min_profit_cents > 0


def test_budget_allocation_3way():
    odds = {
        Outcome.HOME: Decimal("2.10"),
        Outcome.DRAW: Decimal("3.50"),
        Outcome.AWAY: Decimal("4.00"),
    }
    sources = {
        Outcome.HOME: Source.SOURCE_A,
        Outcome.DRAW: Source.SOURCE_B,
        Outcome.AWAY: Source.SOURCE_E,
    }
    result = calculate_budget_allocation(
        total_budget_cents=1_000_000,  # 10.000 TL
        odds=odds,
        sources=sources,
    )
    assert result.is_fixed_stake is False
    assert result.total_budget_cents == 1_000_000
    assert len(result.lines) == 3
    assert result.min_profit_cents > 0

"""Unit tests for hedging and odds drift recovery calculation engine."""

from decimal import Decimal

import pytest

from surebet.domain.enums import Outcome, Source, TwoWayOutcome
from surebet.opportunities.allocation_hedging import (
    FixedLeg,
    HedgingResult,
    calculate_hedging_allocation,
)


def test_hedging_positive_arbitrage_3way() -> None:
    """When market remains profitable after odds change, yields a positive worst-case difference."""
    fixed_outcome = Outcome.HOME
    fixed_odds = Decimal("3.10")
    fixed_stake_cents = 600_000  # 6,000.00 TL

    counter_odds = {
        Outcome.DRAW: Decimal("3.60"),
        Outcome.AWAY: Decimal("3.40"),
    }
    counter_sources = {
        Outcome.DRAW: Source.SOURCE_A,
        Outcome.AWAY: Source.SOURCE_E,
    }

    result = calculate_hedging_allocation(
        fixed_outcome=fixed_outcome,
        fixed_odds=fixed_odds,
        fixed_stake_cents=fixed_stake_cents,
        fixed_source=Source.SOURCE_B,
        counter_odds=counter_odds,
        counter_sources=counter_sources,
        apply_cashback=True,
    )

    assert isinstance(result, HedgingResult)
    assert result.is_profitable is True
    assert result.net_profit_cents > 0
    assert result.profit_bps > 0
    assert result.naked_loss_cents == 600_000
    assert result.saved_amount_cents > 0

    lines_by_outcome = {line.outcome: line for line in result.lines}
    assert len(lines_by_outcome) == 3
    assert lines_by_outcome[Outcome.HOME].stake_cents == 600_000
    assert lines_by_outcome[Outcome.HOME].is_fixed is True
    assert lines_by_outcome[Outcome.DRAW].is_fixed is False
    assert lines_by_outcome[Outcome.AWAY].is_fixed is False

    # Scenario net profit equality check (should be equalized within 2 kuruş)
    payouts = [line.payout_cents for line in result.lines]
    max_diff = max(payouts) - min(payouts)
    assert max_diff <= 2, f"Payouts not equalized: {payouts}"


def test_hedging_negative_arbitrage_3way_loss_minimization() -> None:
    """When market moves against the bet, equalizes and minimizes loss across all outcomes."""
    fixed_outcome = Outcome.HOME
    fixed_odds = Decimal("3.10")
    fixed_stake_cents = 600_000  # 6,000.00 TL

    counter_odds = {
        Outcome.DRAW: Decimal("3.00"),
        Outcome.AWAY: Decimal("2.20"),
    }
    counter_sources = {
        Outcome.DRAW: Source.SOURCE_E,
        Outcome.AWAY: Source.SOURCE_C,
    }

    result = calculate_hedging_allocation(
        fixed_outcome=fixed_outcome,
        fixed_odds=fixed_odds,
        fixed_stake_cents=fixed_stake_cents,
        fixed_source=Source.SOURCE_B,
        counter_odds=counter_odds,
        counter_sources=counter_sources,
        apply_cashback=False,
    )

    assert result.is_profitable is False
    assert result.net_profit_cents < 0
    assert result.profit_bps < 0
    assert result.naked_loss_cents == 600_000
    # Instead of losing 6,000 TL, hedging caps the loss at a much smaller amount, saving money
    assert result.saved_amount_cents > 300_000  # Saved at least 3,000 TL

    # Check that each scenario loss is equalized within 2 kuruş
    payouts = [line.payout_cents for line in result.lines]
    max_diff = max(payouts) - min(payouts)
    assert max_diff <= 2, f"Payouts not equalized: {payouts}"


def test_hedging_2way_over_under() -> None:
    """Supports 2-way markets such as Over/Under."""
    result = calculate_hedging_allocation(
        fixed_outcome=TwoWayOutcome.OVER,
        fixed_odds=Decimal("1.95"),
        fixed_stake_cents=500_000,
        fixed_source=Source.SOURCE_C,
        counter_odds={TwoWayOutcome.UNDER: Decimal("1.90")},
        counter_sources={TwoWayOutcome.UNDER: Source.SOURCE_A},
        apply_cashback=True,
    )

    assert len(result.lines) == 2
    assert result.total_investment_cents > 500_000
    payouts = [line.payout_cents for line in result.lines]
    assert abs(payouts[0] - payouts[1]) <= 2


def test_hedging_cashback_affects_hedging_stakes() -> None:
    """Cashback on losing eligible bets correctly factors into effective odds and payoffs."""
    no_cb = calculate_hedging_allocation(
        fixed_outcome=Outcome.HOME,
        fixed_odds=Decimal("3.00"),
        fixed_stake_cents=300_000,
        fixed_source=Source.SOURCE_E,
        counter_odds={Outcome.DRAW: Decimal("3.20"), Outcome.AWAY: Decimal("3.20")},
        counter_sources={Outcome.DRAW: Source.SOURCE_E, Outcome.AWAY: Source.SOURCE_E},
        apply_cashback=False,
    )

    with_cb = calculate_hedging_allocation(
        fixed_outcome=Outcome.HOME,
        fixed_odds=Decimal("3.00"),
        fixed_stake_cents=300_000,
        fixed_source=Source.SOURCE_A,  # Has 10% cashback
        counter_odds={Outcome.DRAW: Decimal("3.20"), Outcome.AWAY: Decimal("3.20")},
        counter_sources={
            Outcome.DRAW: Source.SOURCE_C,
            Outcome.AWAY: Source.SOURCE_B,
        },  # Both have cashback
        apply_cashback=True,
    )

    # Net profit with cashback should be strictly higher than without cashback
    assert with_cb.net_profit_cents > no_cb.net_profit_cents


def test_hedging_invalid_inputs() -> None:
    """Validation errors for invalid stakes, odds, or mismatched outcomes."""
    with pytest.raises(ValueError, match=r"stake_cents.*must be positive"):
        calculate_hedging_allocation(
            fixed_outcome=Outcome.HOME,
            fixed_odds=Decimal("2.00"),
            fixed_stake_cents=0,
            fixed_source=Source.SOURCE_B,
            counter_odds={Outcome.AWAY: Decimal("2.00")},
            counter_sources={Outcome.AWAY: Source.SOURCE_C},
        )

    with pytest.raises(ValueError, match=r"odds.*must be greater than 1\.0"):
        calculate_hedging_allocation(
            fixed_outcome=Outcome.HOME,
            fixed_odds=Decimal("0.90"),
            fixed_stake_cents=100_000,
            fixed_source=Source.SOURCE_B,
            counter_odds={Outcome.AWAY: Decimal("2.00")},
            counter_sources={Outcome.AWAY: Source.SOURCE_C},
        )


def test_hedging_multiple_fixed_legs_loss_minimization() -> None:
    """When two legs are already placed (e.g. MS1 and X), optimizes the remaining leg (MS2)."""
    fixed_legs = {
        Outcome.HOME: FixedLeg(
            outcome=Outcome.HOME,
            odds=Decimal("3.10"),
            stake_cents=400_000,  # 4,000 TL
            source=Source.SOURCE_B,
        ),
        Outcome.DRAW: FixedLeg(
            outcome=Outcome.DRAW,
            odds=Decimal("3.30"),
            stake_cents=350_000,  # 3,500 TL
            source=Source.SOURCE_A,
        ),
    }

    counter_odds = {Outcome.AWAY: Decimal("2.50")}
    counter_sources = {Outcome.AWAY: Source.SOURCE_C}

    result = calculate_hedging_allocation(
        fixed_legs=fixed_legs,
        counter_odds=counter_odds,
        counter_sources=counter_sources,
        apply_cashback=False,
    )

    assert len(result.lines) == 3
    lines_by_out = {l.outcome: l for l in result.lines}
    assert lines_by_out[Outcome.HOME].stake_cents == 400_000
    assert lines_by_out[Outcome.HOME].is_fixed is True
    assert lines_by_out[Outcome.DRAW].stake_cents == 350_000
    assert lines_by_out[Outcome.DRAW].is_fixed is True

    # Counter leg (MS2) is calculated
    assert lines_by_out[Outcome.AWAY].is_fixed is False
    assert lines_by_out[Outcome.AWAY].stake_cents > 0

    # Naked loss would be 400,000 + 350,000 = 750,000 cents (7,500 TL)
    assert result.naked_loss_cents == 750_000
    # Minimized loss is vastly smaller than 750,000
    assert abs(result.net_profit_cents) < 100_000  # Less than 1,000 TL loss
    assert result.saved_amount_cents > 650_000  # Saved more than 6,500 TL


def test_hedging_multiple_fixed_legs_positive_arbitrage() -> None:
    """When two legs are fixed and remaining leg has generous odds, the margin stays positive."""
    fixed_legs = {
        Outcome.HOME: FixedLeg(
            outcome=Outcome.HOME,
            odds=Decimal("3.10"),
            stake_cents=400_000,
            source=Source.SOURCE_B,
        ),
        Outcome.DRAW: FixedLeg(
            outcome=Outcome.DRAW,
            odds=Decimal("3.30"),
            stake_cents=350_000,
            source=Source.SOURCE_A,
        ),
    }

    counter_odds = {Outcome.AWAY: Decimal("3.20")}  # Generous odds
    counter_sources = {Outcome.AWAY: Source.SOURCE_C}

    result = calculate_hedging_allocation(
        fixed_legs=fixed_legs,
        counter_odds=counter_odds,
        counter_sources=counter_sources,
        apply_cashback=False,
    )

    assert result.is_profitable is True
    assert result.net_profit_cents > 0
    assert result.profit_bps > 0

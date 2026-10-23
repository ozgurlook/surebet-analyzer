from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, getcontext
from fractions import Fraction

import pytest

from surebet.arbitrage.math import equal_return_seed, inverse_sum
from surebet.domain.enums import Outcome

ODDS = {
    Outcome.HOME: Decimal("2.90"),
    Outcome.DRAW: Decimal("3.60"),
    Outcome.AWAY: Decimal("3.10"),
}


def test_inverse_sum_detects_three_way_arbitrage() -> None:
    assert inverse_sum(ODDS) < Decimal(1)


def test_equal_return_seed_spends_exact_budget() -> None:
    stakes = equal_return_seed(ODDS, 100_000)
    assert sum(stakes.values()) == 100_000


def test_equal_return_seed_is_exact_for_arbitrarily_large_budget() -> None:
    budget = 10**80 + 1
    stakes = equal_return_seed(ODDS, budget)
    shares = {outcome: Fraction(1, 1) / Fraction(odds) for outcome, odds in ODDS.items()}
    total = sum(shares.values(), Fraction(0))
    expected = {
        outcome: (budget * share / total).numerator // (budget * share / total).denominator
        for outcome, share in shares.items()
    }
    remainder = budget - sum(expected.values())
    order = sorted(
        Outcome,
        key=lambda outcome: (budget * shares[outcome] / total) - expected[outcome],
        reverse=True,
    )
    for outcome in order[:remainder]:
        expected[outcome] += 1
    assert stakes == expected
    assert sum(stakes.values()) == budget


@pytest.mark.parametrize(
    "odds",
    [
        {Outcome.HOME: Decimal(2), Outcome.DRAW: Decimal(3)},
        {**ODDS, "OTHER": Decimal(2)},
    ],
)
def test_math_rejects_missing_or_extra_outcomes(odds: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        inverse_sum(odds)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "odds", [{**ODDS, Outcome.HOME: Decimal(1)}, {**ODDS, Outcome.HOME: Decimal("NaN")}]
)
def test_math_rejects_invalid_odds(odds: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        inverse_sum(odds)  # type: ignore[arg-type]


def test_math_rejects_non_decimal_odds() -> None:
    with pytest.raises(TypeError):
        inverse_sum({**ODDS, Outcome.HOME: 2.9})  # type: ignore[dict-item]


def test_inverse_sum_is_independent_of_ambient_precision() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 4
        low_precision = inverse_sum(ODDS)
        getcontext().prec = 80
        high_precision = inverse_sum(ODDS)
    finally:
        getcontext().prec = original
    assert low_precision == high_precision


def test_equal_return_seed_uses_outcome_order_for_fraction_ties() -> None:
    odds = {outcome: Decimal(3) for outcome in Outcome}
    stakes = equal_return_seed(odds, 100_001)
    assert stakes == {Outcome.HOME: 33_334, Outcome.DRAW: 33_334, Outcome.AWAY: 33_333}
    reversed_stakes = equal_return_seed(dict(reversed(tuple(odds.items()))), 100_001)
    assert reversed_stakes == stakes


@pytest.mark.parametrize("budget", [-1, True, 1.0, Decimal(1)])
def test_equal_return_seed_rejects_invalid_budget(budget: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        equal_return_seed(ODDS, budget)  # type: ignore[arg-type]


def test_equal_return_seed_accepts_zero_budget() -> None:
    assert equal_return_seed(ODDS, 0) == {outcome: 0 for outcome in Outcome}


def test_math_rejects_odds_with_more_than_four_fractional_digits() -> None:
    odds = {**ODDS, Outcome.HOME: Decimal("2.90001")}
    with pytest.raises(ValueError):
        inverse_sum(odds)
    with pytest.raises(ValueError):
        equal_return_seed(odds, 100_000)


def test_math_owns_decimal_rounding_and_traps() -> None:
    context = getcontext()
    original_rounding = context.rounding
    original_inexact = context.traps[Inexact]
    try:
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        down = inverse_sum(ODDS)
        down_stakes = equal_return_seed(ODDS, 100_000)
        context.rounding = ROUND_UP
        up = inverse_sum(ODDS)
        up_stakes = equal_return_seed(ODDS, 100_000)
    finally:
        context.rounding = original_rounding
        context.traps[Inexact] = original_inexact
    assert down == up
    assert down_stakes == up_stakes

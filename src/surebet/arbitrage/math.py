"""Deterministic Decimal mathematics for three-way standard arbitrage."""

from collections.abc import Mapping
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from surebet.domain.enums import Outcome
from surebet.money import validate_odds

_CALCULATION_PRECISION = 80


def _validated_odds(odds: Mapping[Outcome, Decimal]) -> tuple[Decimal, ...]:
    if not isinstance(odds, Mapping):
        raise TypeError("odds must be a Mapping")
    if set(odds) != set(Outcome):
        raise ValueError("odds must contain exactly HOME, DRAW, and AWAY")

    values: list[Decimal] = []
    for outcome in Outcome:
        value = odds[outcome]
        try:
            values.append(validate_odds(value))
        except (TypeError, ValueError) as error:
            raise type(error)(f"odds for {outcome}: {error}") from error
    return tuple(values)


def inverse_sum(odds: Mapping[Outcome, Decimal]) -> Decimal:
    """Return the inverse-odds sum using a process-precision-independent context."""
    values = _validated_odds(odds)
    exact = sum((Fraction(1, 1) / Fraction(value) for value in values), Fraction(0))
    with localcontext(Context(prec=_CALCULATION_PRECISION)):
        return Decimal(exact.numerator) / Decimal(exact.denominator)


def equal_return_seed(odds: Mapping[Outcome, Decimal], budget_cents: int) -> dict[Outcome, int]:
    """Allocate a nonnegative integer budget by equal theoretical return.

    Exact Decimal shares are floored first; any unallocated cents go to the
    largest fractional shares, with ``Outcome`` declaration order breaking ties.
    """
    _validated_odds(odds)
    if isinstance(budget_cents, bool) or not isinstance(budget_cents, int):
        raise TypeError("budget_cents must be an integer")
    if budget_cents < 0:
        raise ValueError("budget_cents must be nonnegative")
    if budget_cents == 0:
        return {outcome: 0 for outcome in Outcome}

    shares = {outcome: Fraction(1, 1) / Fraction(odds[outcome]) for outcome in Outcome}
    total = sum(shares.values(), Fraction(0))
    exact = {outcome: budget_cents * shares[outcome] / total for outcome in Outcome}
    stakes = {
        outcome: exact[outcome].numerator // exact[outcome].denominator for outcome in Outcome
    }
    remaining = budget_cents - sum(stakes.values())
    order = sorted(
        Outcome,
        key=lambda outcome: exact[outcome] - stakes[outcome],
        reverse=True,
    )
    for outcome in order[:remaining]:
        stakes[outcome] += 1
    if sum(stakes.values()) != budget_cents:
        raise ArithmeticError("equal-return allocation did not spend the full budget")
    return stakes

"""Analytic max-min stake allocation for one three-way assignment.

Replaces a per-assignment CP-SAT model. The continuous problem has a closed
form -- equalizing the three scenario profits puts each stake in proportion to
``1 / (odds - cashback_rate)`` -- and the only thing separating that from the
integer optimum is the kuruş flooring applied to payouts and to cashback. A
bounded steepest-ascent repair over one-kuruş transfers closes that gap.

All arithmetic is integer: odds are scaled to ten-thousandths (the validated
maximum precision), so ``stake * scaled // ODDS_SCALE`` is exactly
``money.payout_cents`` without paying for a ``Fraction`` per evaluation.
"""

from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction

from surebet.domain.enums import Outcome, Source
from surebet.money import odds_scaled

ODDS_SCALE = 10_000
CASHBACK_PERCENT = 10
CASHBACK_SOURCES = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_F)

_OUTCOMES = tuple(Outcome)
_MINIMUM_STAKE_CENTS = 1
# Every improving one-kuruş transfer strictly raises a bounded objective, so the
# ascent terminates on its own. The cap only stops a pathological input from
# spinning; real assignments converge in a handful of steps.
_MAX_REPAIR_STEPS = 256


def _validated(
    odds: Mapping[Outcome, Decimal],
    sources: Mapping[Outcome, Source],
    budget_cents: int,
) -> tuple[tuple[int, ...], tuple[bool, ...]]:
    if not isinstance(odds, Mapping) or not isinstance(sources, Mapping):
        raise TypeError("odds and sources must be Mappings")
    if set(odds) != set(Outcome):
        raise ValueError("odds must contain exactly HOME, DRAW, and AWAY")
    if set(sources) != set(Outcome):
        raise ValueError("sources must contain exactly HOME, DRAW, and AWAY")
    if isinstance(budget_cents, bool) or not isinstance(budget_cents, int):
        raise TypeError("budget_cents must be an integer")
    if budget_cents < _MINIMUM_STAKE_CENTS * len(_OUTCOMES):
        raise ValueError("budget_cents must cover at least one kuruş per outcome")

    scaled = tuple(odds_scaled(odds[outcome], ODDS_SCALE) for outcome in _OUTCOMES)
    eligible = tuple(sources[outcome] in CASHBACK_SOURCES for outcome in _OUTCOMES)
    return scaled, eligible


def _objective(
    stakes: list[int],
    scaled: tuple[int, ...],
    eligible: tuple[bool, ...],
    budget_cents: int,
) -> tuple[int, int]:
    """Worst-case profit first, then total profit across scenarios."""
    worst = None
    total = 0
    for winner in range(len(_OUTCOMES)):
        gross = stakes[winner] * scaled[winner] // ODDS_SCALE
        losing_eligible = sum(
            stakes[loser] for loser in range(len(_OUTCOMES)) if loser != winner and eligible[loser]
        )
        profit = gross + (losing_eligible * CASHBACK_PERCENT) // 100 - budget_cents
        total += profit
        if worst is None or profit < worst:
            worst = profit
    assert worst is not None
    return (worst, total)


def _seed(scaled: tuple[int, ...], eligible: tuple[bool, ...], budget_cents: int) -> list[int]:
    """Proportional equal-profit split of the budget above the per-leg minimum.

    Reserving the minimum stake up front means the proportional share can never
    round an outcome down to zero, so the result is always feasible.
    """
    cashback = Fraction(CASHBACK_PERCENT, 100)
    shares = [
        1 / (Fraction(value, ODDS_SCALE) - (cashback if is_eligible else Fraction(0)))
        for value, is_eligible in zip(scaled, eligible, strict=True)
    ]
    total_share = sum(shares, Fraction(0))
    distributable = budget_cents - _MINIMUM_STAKE_CENTS * len(_OUTCOMES)
    exact = [distributable * share / total_share for share in shares]
    stakes = [_MINIMUM_STAKE_CENTS + value.numerator // value.denominator for value in exact]

    # Largest-remainder, with Outcome declaration order breaking exact ties so
    # the result never depends on mapping iteration order.
    remaining = budget_cents - sum(stakes)
    order = sorted(
        range(len(_OUTCOMES)),
        key=lambda index: (exact[index] - (stakes[index] - _MINIMUM_STAKE_CENTS), -index),
        reverse=True,
    )
    for index in order[:remaining]:
        stakes[index] += 1
    return stakes


def _repair(
    stakes: list[int],
    scaled: tuple[int, ...],
    eligible: tuple[bool, ...],
    budget_cents: int,
) -> list[int]:
    """Steepest ascent over budget-preserving one-kuruş transfers."""
    best_value = _objective(stakes, scaled, eligible, budget_cents)
    for _ in range(_MAX_REPAIR_STEPS):
        best_move: tuple[int, int] | None = None
        for source in range(len(_OUTCOMES)):
            if stakes[source] <= _MINIMUM_STAKE_CENTS:
                continue
            for target in range(len(_OUTCOMES)):
                if source == target:
                    continue
                stakes[source] -= 1
                stakes[target] += 1
                value = _objective(stakes, scaled, eligible, budget_cents)
                stakes[source] += 1
                stakes[target] -= 1
                if value > best_value:
                    best_value, best_move = value, (source, target)
        if best_move is None:
            return stakes
        source, target = best_move
        stakes[source] -= 1
        stakes[target] += 1
    return stakes


def optimal_stakes(
    odds: Mapping[Outcome, Decimal],
    sources: Mapping[Outcome, Source],
    budget_cents: int,
) -> dict[Outcome, int]:
    """Return the max-min kuruş allocation of ``budget_cents`` over the outcomes.

    The allocation spends the budget exactly, gives every outcome at least one
    kuruş, and maximizes the worst scenario profit, breaking ties on the total
    profit across scenarios. Loss-making assignments are allocated and returned
    like any other -- rejecting them is the caller's decision, not this one's.
    """
    scaled, eligible = _validated(odds, sources, budget_cents)
    stakes = _repair(_seed(scaled, eligible, budget_cents), scaled, eligible, budget_cents)
    return {outcome: stakes[index] for index, outcome in enumerate(_OUTCOMES)}

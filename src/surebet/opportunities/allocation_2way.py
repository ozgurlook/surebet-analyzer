"""Analytic max-min stake allocation for two-way assignments with cashback.

Closed-form proportional stake split with bounded steepest-ascent integer repair,
supporting 0%, 10% on one leg, or 10% on both legs (e.g. SOURCE_C + SOURCE_B or SOURCE_C + SOURCE_C).
"""

from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction

from surebet.domain.enums import Source
from surebet.money import odds_scaled

ODDS_SCALE = 10_000
CASHBACK_PERCENT = 10
CASHBACK_SOURCES = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_F)
_MINIMUM_STAKE_CENTS = 1
_MAX_REPAIR_STEPS = 128


def _validated[K](
    odds: Mapping[K, Decimal],
    sources: Mapping[K, Source],
    budget_cents: int,
) -> tuple[tuple[K, ...], tuple[int, ...], tuple[bool, ...]]:
    if not isinstance(odds, Mapping) or not isinstance(sources, Mapping):
        raise TypeError("odds and sources must be Mappings")
    if len(odds) != 2 or len(sources) != 2 or set(odds) != set(sources):
        raise ValueError("odds and sources must contain exactly 2 outcomes")
    if isinstance(budget_cents, bool) or not isinstance(budget_cents, int):
        raise TypeError("budget_cents must be an integer")
    if budget_cents < _MINIMUM_STAKE_CENTS * 2:
        raise ValueError("budget_cents must cover at least one kuruş per outcome")

    outcomes = tuple(odds.keys())
    scaled = tuple(odds_scaled(odds[outcome], ODDS_SCALE) for outcome in outcomes)
    eligible = tuple(sources[outcome] in CASHBACK_SOURCES for outcome in outcomes)
    return outcomes, scaled, eligible


def _objective(
    stakes: list[int],
    scaled: tuple[int, ...],
    eligible: tuple[bool, ...],
    budget_cents: int,
) -> tuple[int, int]:
    """Worst-case profit first, then total profit across both scenarios."""
    worst = None
    total = 0
    for winner in range(2):
        loser = 1 - winner
        gross = stakes[winner] * scaled[winner] // ODDS_SCALE
        losing_eligible = stakes[loser] if eligible[loser] else 0
        cashback = (losing_eligible * CASHBACK_PERCENT) // 100
        profit = gross + cashback - budget_cents
        total += profit
        if worst is None or profit < worst:
            worst = profit
    assert worst is not None
    return (worst, total)


def _seed(scaled: tuple[int, ...], eligible: tuple[bool, ...], budget_cents: int) -> list[int]:
    cashback = Fraction(CASHBACK_PERCENT, 100)
    shares = [
        1 / (Fraction(value, ODDS_SCALE) - (cashback if is_eligible else Fraction(0)))
        for value, is_eligible in zip(scaled, eligible, strict=True)
    ]
    total_share = sum(shares, Fraction(0))
    distributable = budget_cents - _MINIMUM_STAKE_CENTS * 2
    exact = [distributable * share / total_share for share in shares]
    stakes = [_MINIMUM_STAKE_CENTS + value.numerator // value.denominator for value in exact]

    remaining = budget_cents - sum(stakes)
    if remaining > 0:
        remainders = [
            (value - (value.numerator // value.denominator), index)
            for index, value in enumerate(exact)
        ]
        remainders.sort(key=lambda item: item[0], reverse=True)
        for _, index in remainders[:remaining]:
            stakes[index] += 1

    return stakes


def optimal_stakes_2way[K](
    odds: Mapping[K, Decimal],
    sources: Mapping[K, Source],
    budget_cents: int,
) -> dict[K, int]:
    """Calculate the max-min kuruş stake allocation for a two-way market."""
    outcomes, scaled, eligible = _validated(odds, sources, budget_cents)
    stakes = _seed(scaled, eligible, budget_cents)
    current_score = _objective(stakes, scaled, eligible, budget_cents)

    for _ in range(_MAX_REPAIR_STEPS):
        best_step: tuple[int, int] | None = None
        best_score = current_score

        # Either move 1 kuruş from leg 0 to leg 1, or from leg 1 to leg 0
        for src, dst in ((0, 1), (1, 0)):
            if stakes[src] <= _MINIMUM_STAKE_CENTS:
                continue
            stakes[src] -= 1
            stakes[dst] += 1
            score = _objective(stakes, scaled, eligible, budget_cents)
            if score > best_score:
                best_score = score
                best_step = (src, dst)
            stakes[src] += 1
            stakes[dst] -= 1

        if best_step is None:
            break

        src, dst = best_step
        stakes[src] -= 1
        stakes[dst] += 1
        current_score = best_score

    return dict(zip(outcomes, stakes, strict=True))

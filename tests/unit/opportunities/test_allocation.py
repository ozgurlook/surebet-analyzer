"""Ground-truth tests for the analytic max-min stake allocator.

The allocator replaces a CP-SAT model, so these tests never compare against
CP-SAT: they compare against an exhaustive search over every integer allocation
of a small budget, which is the definition the CP-SAT model was encoding.
"""

from decimal import Decimal
from itertools import product

import pytest

from surebet.domain.enums import Outcome, Source
from surebet.money import payout_cents
from surebet.opportunities.allocation import CASHBACK_SOURCES, optimal_stakes


def _objective(
    stakes: dict[Outcome, int],
    odds: dict[Outcome, Decimal],
    sources: dict[Outcome, Source],
    budget_cents: int,
) -> tuple[int, int]:
    """The lexicographic objective: worst-case profit first, then total profit.

    Mirrors the payout and cashback arithmetic the strategy engine settles on
    post-solve, so a winning allocation here is winning by the same yardstick
    the opportunity rows are ultimately scored with.
    """
    profits = []
    for winner in Outcome:
        gross = payout_cents(stakes[winner], odds[winner])
        losing_eligible = sum(
            stakes[loser]
            for loser in Outcome
            if loser is not winner and sources[loser] in CASHBACK_SOURCES
        )
        profits.append(gross + (losing_eligible * 10) // 100 - budget_cents)
    return (min(profits), sum(profits))


def _brute_force(
    odds: dict[Outcome, Decimal],
    sources: dict[Outcome, Source],
    budget_cents: int,
) -> tuple[int, int]:
    """Best achievable objective over every allocation giving each outcome >= 1."""
    best = None
    for home in range(1, budget_cents - 1):
        for draw in range(1, budget_cents - home):
            away = budget_cents - home - draw
            if away < 1:
                continue
            stakes = {Outcome.HOME: home, Outcome.DRAW: draw, Outcome.AWAY: away}
            value = _objective(stakes, odds, sources, budget_cents)
            if best is None or value > best:
                best = value
    assert best is not None
    return best


def _case(
    prices: tuple[str, str, str], books: tuple[Source, Source, Source]
) -> tuple[dict[Outcome, Decimal], dict[Outcome, Source]]:
    odds = {outcome: Decimal(p) for outcome, p in zip(Outcome, prices, strict=True)}
    sources = dict(zip(Outcome, books, strict=True))
    return odds, sources


ARBITRAGE = _case(("2.90", "3.60", "3.10"), (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C))
CASHBACK_ONLY = _case(("2.10", "3.40", "3.30"), (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_C))
NO_CASHBACK = _case(("2.50", "3.30", "3.00"), (Source.SOURCE_A, Source.SOURCE_D, Source.SOURCE_A))
LOSS_MAKING = _case(("1.80", "3.20", "3.90"), (Source.SOURCE_A, Source.SOURCE_D, Source.SOURCE_A))
LOPSIDED = _case(("1.20", "6.50", "12.00"), (Source.SOURCE_C, Source.SOURCE_A, Source.SOURCE_B))

ALL_CASES = {
    "arbitrage": ARBITRAGE,
    "cashback_only": CASHBACK_ONLY,
    "no_cashback": NO_CASHBACK,
    "loss_making": LOSS_MAKING,
    "lopsided": LOPSIDED,
}


@pytest.mark.parametrize("name", sorted(ALL_CASES))
def test_optimal_stakes_spend_the_whole_budget(name: str) -> None:
    odds, sources = ALL_CASES[name]
    stakes = optimal_stakes(odds, sources, 100_000)
    assert sum(stakes.values()) == 100_000
    assert set(stakes) == set(Outcome)


@pytest.mark.parametrize("name", sorted(ALL_CASES))
def test_optimal_stakes_give_every_outcome_at_least_one_kurus(name: str) -> None:
    odds, sources = ALL_CASES[name]
    stakes = optimal_stakes(odds, sources, 100_000)
    assert all(value >= 1 for value in stakes.values())


@pytest.mark.parametrize("name", sorted(ALL_CASES))
@pytest.mark.parametrize("budget_cents", [300, 617])
def test_optimal_stakes_beat_every_other_allocation(name: str, budget_cents: int) -> None:
    """The allocator's objective must equal the exhaustive-search optimum."""
    odds, sources = ALL_CASES[name]
    stakes = optimal_stakes(odds, sources, budget_cents)
    assert _objective(stakes, odds, sources, budget_cents) == _brute_force(
        odds, sources, budget_cents
    )


@pytest.mark.parametrize("name", sorted(ALL_CASES))
def test_optimal_stakes_are_deterministic_regardless_of_mapping_order(name: str) -> None:
    odds, sources = ALL_CASES[name]
    reversed_odds = dict(reversed(tuple(odds.items())))
    reversed_sources = dict(reversed(tuple(sources.items())))
    assert optimal_stakes(odds, sources, 50_000) == optimal_stakes(
        reversed_odds, reversed_sources, 50_000
    )


def test_optimal_stakes_still_allocate_a_loss_making_assignment() -> None:
    """Negative assignments must be allocated and scored, never rejected.

    The product shows a percentage for every tracked-league match, including
    the ones that cannot turn a profit.
    """
    odds, sources = LOSS_MAKING
    stakes = optimal_stakes(odds, sources, 100_000)
    worst, _ = _objective(stakes, odds, sources, 100_000)
    assert worst < 0
    assert sum(stakes.values()) == 100_000


def test_optimal_stakes_reject_a_budget_below_one_kurus_per_outcome() -> None:
    odds, sources = ARBITRAGE
    with pytest.raises(ValueError):
        optimal_stakes(odds, sources, 2)


def test_optimal_stakes_equalize_returns_when_no_cashback_applies() -> None:
    """Without cashback the max-min allocation is the classic equal-return split."""
    odds, sources = NO_CASHBACK
    stakes = optimal_stakes(odds, sources, 100_000)
    returns = [payout_cents(stakes[outcome], odds[outcome]) for outcome in Outcome]
    assert max(returns) - min(returns) <= 3


def test_optimal_stakes_exhaust_the_budget_for_every_small_budget() -> None:
    odds, sources = CASHBACK_ONLY
    for budget_cents in range(3, 200):
        stakes = optimal_stakes(odds, sources, budget_cents)
        assert sum(stakes.values()) == budget_cents
        assert all(value >= 1 for value in stakes.values())


def test_optimal_stakes_match_brute_force_across_a_budget_sweep() -> None:
    """Guards the floor()-driven corrections the proportional seed alone misses."""
    odds, sources = CASHBACK_ONLY
    for budget_cents in range(50, 260, 7):
        stakes = optimal_stakes(odds, sources, budget_cents)
        assert _objective(stakes, odds, sources, budget_cents) == _brute_force(
            odds, sources, budget_cents
        ), f"budget={budget_cents}"


def test_optimal_stakes_reject_unknown_outcome_mappings() -> None:
    odds, sources = ARBITRAGE
    partial = {Outcome.HOME: odds[Outcome.HOME]}
    with pytest.raises(ValueError):
        optimal_stakes(partial, sources, 100_000)


def test_optimal_stakes_cover_every_source_combination() -> None:
    """Cashback eligibility varies per leg; none of the 4^3 shapes may crash."""
    books = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_A, Source.SOURCE_D)
    odds = {
        outcome: Decimal(p) for outcome, p in zip(Outcome, ("2.40", "3.50", "3.20"), strict=True)
    }
    for combination in product(books, repeat=3):
        sources = dict(zip(Outcome, combination, strict=True))
        stakes = optimal_stakes(odds, sources, 10_000)
        assert sum(stakes.values()) == 10_000


def test_source_f_in_cashback_sources() -> None:
    assert Source.SOURCE_F in CASHBACK_SOURCES
    assert Source.SOURCE_E not in CASHBACK_SOURCES

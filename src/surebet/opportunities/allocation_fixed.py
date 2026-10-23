"""Fixed-stake and dynamic budget allocation calculator.

Allows fixing one outcome stake (e.g. MS1 = 3500 TL) and calculating optimal kurus stakes
for other outcomes to equalize scenario profit in 2-way and 3-way markets, or re-solving
for a specific total budget.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from surebet.domain.enums import Outcome, Source, TwoWayOutcome
from surebet.money import odds_scaled
from surebet.opportunities.allocation import (
    CASHBACK_PERCENT,
    CASHBACK_SOURCES,
    ODDS_SCALE,
)
from surebet.opportunities.allocation import (
    optimal_stakes as optimal_stakes_3way,
)
from surebet.opportunities.allocation_2way import optimal_stakes_2way


@dataclass(frozen=True)
class StakeLine:
    outcome: Outcome | TwoWayOutcome
    source: Source
    odds: Decimal
    stake_cents: int
    payout_cents: int


@dataclass(frozen=True)
class AllocationResult:
    is_fixed_stake: bool
    fixed_outcome: Outcome | TwoWayOutcome | None
    total_budget_cents: int
    min_profit_cents: int
    profit_bps: int
    lines: tuple[StakeLine, ...]


def calculate_fixed_stake_allocation(
    *,
    fixed_outcome: Outcome | TwoWayOutcome,
    fixed_stake_cents: int,
    odds: Mapping[Outcome | TwoWayOutcome, Decimal],
    sources: Mapping[Outcome | TwoWayOutcome, Source],
    apply_cashback: bool = True,
) -> AllocationResult:
    """Calculate optimal integer kurus stakes when one outcome's stake is fixed."""
    if fixed_stake_cents <= 0:
        raise ValueError("fixed_stake_cents must be positive")
    if fixed_outcome not in odds:
        raise ValueError(f"fixed_outcome {fixed_outcome} not in odds")

    outcomes = tuple(odds.keys())
    fixed_idx = outcomes.index(fixed_outcome)
    scaled = tuple(odds_scaled(odds[o], ODDS_SCALE) for o in outcomes)
    eligible = tuple(
        (sources.get(o) in CASHBACK_SOURCES) if apply_cashback else False for o in outcomes
    )
    cb = Fraction(CASHBACK_PERCENT, 100) if apply_cashback else Fraction(0)
    shares = [
        1 / (Fraction(val, ODDS_SCALE) - (cb if is_el else Fraction(0)))
        for val, is_el in zip(scaled, eligible, strict=True)
    ]
    total_share = sum(shares, Fraction(0))

    # Initial total budget corresponding to fixing fixed_outcome's stake
    budget = round(fixed_stake_cents * total_share / shares[fixed_idx])

    stakes: dict[Outcome | TwoWayOutcome, int] = {}
    if set(outcomes) == set(Outcome):
        assert isinstance(fixed_outcome, Outcome)
        odds_3way = {o: odds[o] for o in Outcome}
        sources_3way = {o: sources[o] for o in Outcome}
        solved_3way = optimal_stakes_3way(odds_3way, sources_3way, budget)
        diff = fixed_stake_cents - solved_3way[fixed_outcome]
        if diff != 0:
            budget += diff
            solved_3way = optimal_stakes_3way(odds_3way, sources_3way, budget)
        diff_final = fixed_stake_cents - solved_3way[fixed_outcome]
        if diff_final != 0:
            solved_3way[fixed_outcome] += diff_final
        for o3, v3 in solved_3way.items():
            stakes[o3] = v3
    elif set(outcomes) == set(TwoWayOutcome):
        assert isinstance(fixed_outcome, TwoWayOutcome)
        odds_2way: dict[Outcome | TwoWayOutcome, Decimal] = {o: odds[o] for o in TwoWayOutcome}
        sources_2way: dict[Outcome | TwoWayOutcome, Source] = {o: sources[o] for o in TwoWayOutcome}
        solved_2way = optimal_stakes_2way(odds_2way, sources_2way, budget)
        diff = fixed_stake_cents - solved_2way[fixed_outcome]
        if diff != 0:
            budget += diff
            solved_2way = optimal_stakes_2way(odds_2way, sources_2way, budget)
        diff_final = fixed_stake_cents - solved_2way[fixed_outcome]
        if diff_final != 0:
            solved_2way[fixed_outcome] += diff_final
        for o2, v2 in solved_2way.items():
            stakes[o2] = v2
    else:
        for idx, o in enumerate(outcomes):
            if o == fixed_outcome:
                stakes[o] = fixed_stake_cents
            else:
                stakes[o] = max(1, round(fixed_stake_cents * shares[idx] / shares[fixed_idx]))

    total_budget = sum(stakes.values())

    # Compute scenario payoffs
    lines: list[StakeLine] = []
    min_profit_cents = None

    for outcome in outcomes:
        st = stakes[outcome]
        sc_odds = odds[outcome]
        sc_scaled = odds_scaled(sc_odds, ODDS_SCALE)
        gross = (st * sc_scaled) // ODDS_SCALE

        cashback = 0
        if apply_cashback:
            losing_eligible = sum(
                stakes[other]
                for other in outcomes
                if other != outcome and sources.get(other) in CASHBACK_SOURCES
            )
            cashback = (losing_eligible * CASHBACK_PERCENT) // 100

        payout = gross + cashback
        profit = payout - total_budget
        if min_profit_cents is None or profit < min_profit_cents:
            min_profit_cents = profit

        lines.append(
            StakeLine(
                outcome=outcome,
                source=sources[outcome],
                odds=sc_odds,
                stake_cents=st,
                payout_cents=payout,
            )
        )

    if min_profit_cents is None:
        min_profit_cents = 0
    profit_bps = (min_profit_cents * 10_000) // total_budget if total_budget > 0 else 0

    return AllocationResult(
        is_fixed_stake=True,
        fixed_outcome=fixed_outcome,
        total_budget_cents=total_budget,
        min_profit_cents=min_profit_cents,
        profit_bps=profit_bps,
        lines=tuple(lines),
    )


def calculate_budget_allocation(
    *,
    total_budget_cents: int,
    odds: Mapping[Outcome | TwoWayOutcome, Decimal],
    sources: Mapping[Outcome | TwoWayOutcome, Source],
) -> AllocationResult:
    """Re-solve stake allocation for a specified total budget."""
    if total_budget_cents <= 0:
        raise ValueError("total_budget_cents must be positive")

    outcomes = tuple(odds.keys())
    stakes: dict[Outcome | TwoWayOutcome, int] = {}
    if set(outcomes) == set(Outcome):
        # 3-way
        odds_3way = {o: odds[o] for o in Outcome}
        sources_3way = {o: sources[o] for o in Outcome}
        for o3, v3 in optimal_stakes_3way(odds_3way, sources_3way, total_budget_cents).items():
            stakes[o3] = v3
    elif set(outcomes) == set(TwoWayOutcome):
        # 2-way
        odds_2way: dict[Outcome | TwoWayOutcome, Decimal] = {o: odds[o] for o in TwoWayOutcome}
        sources_2way: dict[Outcome | TwoWayOutcome, Source] = {o: sources[o] for o in TwoWayOutcome}
        for o2, v2 in optimal_stakes_2way(odds_2way, sources_2way, total_budget_cents).items():
            stakes[o2] = v2
    else:
        # Generic proportional
        return calculate_fixed_stake_allocation(
            fixed_outcome=outcomes[0],
            fixed_stake_cents=total_budget_cents // len(outcomes),
            odds=odds,
            sources=sources,
        )

    actual_budget = sum(stakes.values())
    lines: list[StakeLine] = []
    min_profit_cents = None

    for outcome in outcomes:
        st = stakes[outcome]
        sc_odds = odds[outcome]
        sc_scaled = odds_scaled(sc_odds, ODDS_SCALE)
        gross = (st * sc_scaled) // ODDS_SCALE
        losing_eligible = sum(
            stakes[other]
            for other in outcomes
            if other != outcome and sources.get(other) in CASHBACK_SOURCES
        )
        cashback = (losing_eligible * CASHBACK_PERCENT) // 100
        payout = gross + cashback
        profit = payout - actual_budget
        if min_profit_cents is None or profit < min_profit_cents:
            min_profit_cents = profit

        lines.append(
            StakeLine(
                outcome=outcome,
                source=sources[outcome],
                odds=sc_odds,
                stake_cents=st,
                payout_cents=payout,
            )
        )

    if min_profit_cents is None:
        min_profit_cents = 0
    profit_bps = (min_profit_cents * 10_000) // actual_budget if actual_budget > 0 else 0

    return AllocationResult(
        is_fixed_stake=False,
        fixed_outcome=None,
        total_budget_cents=actual_budget,
        min_profit_cents=min_profit_cents,
        profit_bps=profit_bps,
        lines=tuple(lines),
    )

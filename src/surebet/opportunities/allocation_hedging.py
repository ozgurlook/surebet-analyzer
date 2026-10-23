"""Hedging and odds drift recovery calculation engine.

Calculates optimal counter-stakes when one or more legs have already been executed at a bookmaker,
either keeping a positive arbitrage margin if the market remains favorable, or minimizing
and equalizing net loss across all scenarios if odds slipped/drifted.
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


@dataclass(frozen=True)
class FixedLeg:
    """Specification of an already placed/executed bet leg."""

    outcome: Outcome | TwoWayOutcome
    odds: Decimal
    stake_cents: int
    source: Source


@dataclass(frozen=True)
class HedgingLine:
    """Individual leg allocation result in a hedging calculation."""

    outcome: Outcome | TwoWayOutcome
    source: Source
    odds: Decimal
    stake_cents: int
    payout_cents: int
    is_fixed: bool


@dataclass(frozen=True)
class HedgingResult:
    """Comprehensive hedging allocation result across all outcomes."""

    is_profitable: bool
    total_investment_cents: int
    min_payout_cents: int
    net_profit_cents: int
    profit_bps: int
    naked_loss_cents: int
    saved_amount_cents: int
    lines: tuple[HedgingLine, ...]


def calculate_hedging_allocation(
    *,
    fixed_legs: Mapping[Outcome | TwoWayOutcome, FixedLeg] | None = None,
    fixed_outcome: Outcome | TwoWayOutcome | None = None,
    fixed_odds: Decimal | None = None,
    fixed_stake_cents: int | None = None,
    fixed_source: Source | None = None,
    counter_odds: Mapping[Outcome | TwoWayOutcome, Decimal],
    counter_sources: Mapping[Outcome | TwoWayOutcome, Source],
    apply_cashback: bool = True,
) -> HedgingResult:
    """Calculate optimal kurus counter-stakes when one or more outcomes have already been placed."""
    if fixed_legs is not None:
        resolved_fixed_legs = dict(fixed_legs)
    else:
        if (
            fixed_outcome is None
            or fixed_odds is None
            or fixed_stake_cents is None
            or fixed_source is None
        ):
            raise ValueError(
                "Either fixed_legs or (fixed_outcome, fixed_odds, fixed_stake_cents, fixed_source) must be provided"
            )
        resolved_fixed_legs = {
            fixed_outcome: FixedLeg(
                outcome=fixed_outcome,
                odds=fixed_odds,
                stake_cents=fixed_stake_cents,
                source=fixed_source,
            )
        }

    if not resolved_fixed_legs:
        raise ValueError("At least one fixed leg must be provided")

    for f_out, leg in resolved_fixed_legs.items():
        if leg.stake_cents <= 0:
            raise ValueError(f"stake_cents for {f_out} must be positive (got {leg.stake_cents})")
        if leg.odds <= Decimal("1.0"):
            raise ValueError(f"odds for {f_out} must be greater than 1.0 (got {leg.odds})")
        if f_out in counter_odds:
            raise ValueError(f"fixed outcome {f_out} cannot be in counter_odds")

    if not counter_odds:
        raise ValueError("counter_odds cannot be empty")
    if set(counter_odds.keys()) != set(counter_sources.keys()):
        raise ValueError("counter_odds and counter_sources must have identical outcome keys")

    for outcome, odd in counter_odds.items():
        if odd <= Decimal("1.0"):
            raise ValueError(f"counter odds for {outcome} must be greater than 1.0 (got {odd})")

    fixed_outcomes = tuple(resolved_fixed_legs.keys())
    counter_outcomes = tuple(counter_odds.keys())
    all_outcomes = (*fixed_outcomes, *counter_outcomes)

    odds_map: dict[Outcome | TwoWayOutcome, Decimal] = {
        **{f_out: leg.odds for f_out, leg in resolved_fixed_legs.items()},
        **counter_odds,
    }
    sources_map: dict[Outcome | TwoWayOutcome, Source] = {
        **{f_out: leg.source for f_out, leg in resolved_fixed_legs.items()},
        **counter_sources,
    }

    # Effective continuous odds accounting for losing cashback
    cb = Fraction(CASHBACK_PERCENT, 100) if apply_cashback else Fraction(0)
    eff_odds: dict[Outcome | TwoWayOutcome, Fraction] = {}
    for outcome in all_outcomes:
        val = odds_scaled(odds_map[outcome], ODDS_SCALE)
        is_eligible = (sources_map[outcome] in CASHBACK_SOURCES) if apply_cashback else False
        eff = Fraction(val, ODDS_SCALE) - (cb if is_eligible else Fraction(0))
        if eff <= 0:
            raise ValueError(f"Effective odds for {outcome} must be positive")
        eff_odds[outcome] = eff

    # Target return is bounded by the minimum return among already placed legs
    target_return = min(
        leg.stake_cents * eff_odds[f_out] for f_out, leg in resolved_fixed_legs.items()
    )

    # Initial continuous seed stakes rounded to nearest kurus
    stakes: dict[Outcome | TwoWayOutcome, int] = {
        f_out: leg.stake_cents for f_out, leg in resolved_fixed_legs.items()
    }
    for c in counter_outcomes:
        raw = round(target_return / eff_odds[c])
        stakes[c] = max(1, raw)

    def _eval_scenario_payouts(
        curr_stakes: Mapping[Outcome | TwoWayOutcome, int],
    ) -> dict[Outcome | TwoWayOutcome, int]:
        payouts: dict[Outcome | TwoWayOutcome, int] = {}
        for winner in all_outcomes:
            sc_val = odds_scaled(odds_map[winner], ODDS_SCALE)
            gross = (curr_stakes[winner] * sc_val) // ODDS_SCALE
            cashback = 0
            if apply_cashback:
                losing_eligible = sum(
                    curr_stakes[loser]
                    for loser in all_outcomes
                    if loser != winner and sources_map[loser] in CASHBACK_SOURCES
                )
                cashback = (losing_eligible * CASHBACK_PERCENT) // 100
            payouts[winner] = gross + cashback
        return payouts

    def _eval_objective(
        curr_stakes: Mapping[Outcome | TwoWayOutcome, int],
    ) -> tuple[int, int]:
        payouts = _eval_scenario_payouts(curr_stakes)
        total_inv = sum(curr_stakes.values())
        worst = None
        total = 0
        for p in payouts.values():
            profit = p - total_inv
            total += profit
            if worst is None or profit < worst:
                worst = profit
        assert worst is not None
        return (worst, total)

    # Steepest-ascent bounded repair on counter stakes to close discretization gaps
    steps = 0
    improved = True
    while improved and steps < 64:
        improved = False
        steps += 1
        current_obj = _eval_objective(stakes)
        best_obj = current_obj
        best_candidate: tuple[Outcome | TwoWayOutcome, int] | None = None

        for c in counter_outcomes:
            for delta in (-1, 1):
                if stakes[c] + delta < 1:
                    continue
                stakes[c] += delta
                obj = _eval_objective(stakes)
                if obj > best_obj:
                    best_obj = obj
                    best_candidate = (c, delta)
                stakes[c] -= delta

        if best_candidate is not None:
            c, delta = best_candidate
            stakes[c] += delta
            improved = True

    final_payouts = _eval_scenario_payouts(stakes)
    total_investment = sum(stakes.values())
    min_payout = min(final_payouts.values())
    net_profit = min_payout - total_investment
    profit_bps = (net_profit * 10_000) // total_investment if total_investment > 0 else 0
    is_profitable = net_profit > 0
    naked_loss = sum(leg.stake_cents for leg in resolved_fixed_legs.values())
    saved_amount = naked_loss + net_profit

    lines = tuple(
        HedgingLine(
            outcome=outcome,
            source=sources_map[outcome],
            odds=odds_map[outcome],
            stake_cents=stakes[outcome],
            payout_cents=final_payouts[outcome],
            is_fixed=(outcome in resolved_fixed_legs),
        )
        for outcome in all_outcomes
    )

    return HedgingResult(
        is_profitable=is_profitable,
        total_investment_cents=total_investment,
        min_payout_cents=min_payout,
        net_profit_cents=net_profit,
        profit_bps=profit_bps,
        naked_loss_cents=naked_loss,
        saved_amount_cents=saved_amount,
        lines=lines,
    )

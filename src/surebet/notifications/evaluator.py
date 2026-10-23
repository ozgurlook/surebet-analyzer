"""Both-policy evaluation, exact threshold checks, and deterministic selection."""

from collections.abc import Callable
from datetime import datetime

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.matching.models import MatchedEvent
from surebet.notifications.cards.thresholds import meets_threshold, profit_basis_points
from surebet.opportunities.strategy import CalculatedOpportunity, calculate_event_opportunities

from .models import DualPolicyEvaluation

__all__ = [
    "Calculator",
    "choose_policy",
    "evaluate_event_by_market",
    "meets_threshold",
    "profit_basis_points",
    "uses_source_a_match_sides",
]

Calculator = Callable[..., tuple[CalculatedOpportunity, ...]]


def _source_a_match_side_count(opportunity: CalculatedOpportunity) -> int:
    return sum(
        1
        for outcome, selection in opportunity.selections.items()
        if outcome in (Outcome.HOME, Outcome.AWAY) and selection.source is Source.SOURCE_A
    )


def uses_source_a_match_sides(opportunity: CalculatedOpportunity) -> bool:
    return _source_a_match_side_count(opportunity) > 0


def _selection_key(opportunity: CalculatedOpportunity) -> tuple[int, int, str]:
    return (
        -opportunity.min_adjusted_profit_cents,
        _source_a_match_side_count(opportunity),
        opportunity.opportunity_id,
    )


def choose_policy(
    draw_only: CalculatedOpportunity, wide: CalculatedOpportunity
) -> DualPolicyEvaluation:
    selected = min((draw_only, wide), key=_selection_key)
    selected_uses_source_a_sides = uses_source_a_match_sides(selected)

    relevant_sources = frozenset(selection.source for selection in selected.selections.values())
    if selected_uses_source_a_sides:
        relevant_sources = relevant_sources | frozenset(
            selection.source for selection in draw_only.selections.values()
        )

    return DualPolicyEvaluation(
        matched_id=selected.matched_id,
        market_type=selected.market_type,
        selected=selected,
        draw_only=draw_only,
        uses_source_a_match_sides=selected_uses_source_a_sides,
        relevant_sources=relevant_sources,
    )


def _group_by_market(
    candidates: tuple[CalculatedOpportunity, ...],
) -> dict[MarketType, CalculatedOpportunity]:
    best: dict[MarketType, CalculatedOpportunity] = {}
    for candidate in candidates:
        current = best.get(candidate.market_type)
        if current is None or _selection_key(candidate) < _selection_key(current):
            best[candidate.market_type] = candidate
    return best


def evaluate_event_by_market(
    event: MatchedEvent,
    budget_cents: int,
    now: datetime,
    stale_after_seconds: int,
    *,
    calculator: Calculator = calculate_event_opportunities,
) -> dict[MarketType, DualPolicyEvaluation]:
    """One evaluation per market.

    Pairing draw-only against wide within the same market matters: comparing the
    global bests could pit a 1X2 candidate against a BTTS one and make the Source A
    comparison block describe two unrelated bets.
    """
    draw_best = _group_by_market(
        calculator(
            event=event,
            budget_cents=budget_cents,
            include_source_a_match_sides=False,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
    )
    wide_best = _group_by_market(
        calculator(
            event=event,
            budget_cents=budget_cents,
            include_source_a_match_sides=True,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
    )

    evaluations: dict[MarketType, DualPolicyEvaluation] = {}
    for market_type in draw_best.keys() | wide_best.keys():
        draw_only = draw_best.get(market_type) or wide_best[market_type]
        wide = wide_best.get(market_type) or draw_only
        evaluations[market_type] = choose_policy(draw_only, wide)
    return evaluations

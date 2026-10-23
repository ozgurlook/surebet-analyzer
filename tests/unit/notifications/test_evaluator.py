from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.matching.models import MatchedEvent
from surebet.notifications.evaluator import (
    choose_policy,
    evaluate_event_by_market,
    meets_threshold,
    profit_basis_points,
    uses_source_a_match_sides,
)
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.taxonomy.models import TaxonomyStatus

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _selection(outcome: Outcome, source: Source, odds: str) -> StrategySelection:
    return StrategySelection(
        outcome=outcome,
        source=source,
        odds=Decimal(odds),
        stake_cents=1_000,
        cashback_eligible=source in (Source.SOURCE_C, Source.SOURCE_B),
    )


def opportunity_with(
    *,
    opportunity_id: str = "event-1:source_c:source_a:source_b",
    budget_cents: int = 10_000_000,
    profit_cents: int = 0,
    market_type: MarketType = MarketType.FULL_TIME_RESULT,
    include_source_a_match_sides: bool = False,
    selections: Mapping[Outcome, StrategySelection] | None = None,
) -> CalculatedOpportunity:
    if selections is None:
        selections = {
            Outcome.HOME: _selection(Outcome.HOME, Source.SOURCE_C, "2.10"),
            Outcome.DRAW: _selection(Outcome.DRAW, Source.SOURCE_A, "3.40"),
            Outcome.AWAY: _selection(Outcome.AWAY, Source.SOURCE_B, "2.80"),
        }
    profit_percent = round(profit_cents / budget_cents * 100, 2)
    return CalculatedOpportunity.model_construct(
        opportunity_id=opportunity_id,
        matched_id="event-1",
        market_type=market_type,
        market_label=market_type.value,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        budget_cents=budget_cents,
        include_source_a_match_sides=include_source_a_match_sides,
        selections=MappingProxyType(dict(selections)),
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_cents,
        min_adjusted_profit_percent=profit_percent,
        min_standard_profit_cents=profit_cents,
        min_standard_profit_percent=profit_percent,
        is_positive=profit_cents > 0,
        oldest_quote_age_seconds=10,
        calculated_at=NOW,
    )


def _opportunity(
    profit_bps: int,
    market_type: MarketType = MarketType.FULL_TIME_RESULT,
    *,
    budget_cents: int = 1_000_000,
) -> CalculatedOpportunity:
    profit_cents = profit_bps * budget_cents // 10_000
    return opportunity_with(
        opportunity_id=f"event-1:{market_type.value}:{profit_bps}",
        budget_cents=budget_cents,
        profit_cents=profit_cents,
        market_type=market_type,
    )


def draw_only_opportunity(profit: int = -28_000) -> CalculatedOpportunity:
    return opportunity_with(
        opportunity_id="event-1:source_c:source_a:source_b",
        profit_cents=profit,
        include_source_a_match_sides=False,
        selections={
            Outcome.HOME: _selection(Outcome.HOME, Source.SOURCE_C, "2.10"),
            Outcome.DRAW: _selection(Outcome.DRAW, Source.SOURCE_A, "3.40"),
            Outcome.AWAY: _selection(Outcome.AWAY, Source.SOURCE_B, "2.80"),
        },
    )


def wide_opportunity(profit: int = 134_000) -> CalculatedOpportunity:
    return opportunity_with(
        opportunity_id="event-1:source_a:source_c:source_b",
        profit_cents=profit,
        include_source_a_match_sides=True,
        selections={
            Outcome.HOME: _selection(Outcome.HOME, Source.SOURCE_A, "2.20"),
            Outcome.DRAW: _selection(Outcome.DRAW, Source.SOURCE_C, "3.40"),
            Outcome.AWAY: _selection(Outcome.AWAY, Source.SOURCE_B, "2.80"),
        },
    )


EVENT = MatchedEvent(
    matched_id="event-1",
    taxonomy_status=TaxonomyStatus.VERIFIED,
    competition="Premier League",
    country_id="country:GB",
    country_name="United Kingdom",
    country_iso_code="GB",
    league_id="league:00000000-0000-4000-8000-000000000002",
    league_name="Premier League",
    home="Arsenal",
    away="Chelsea",
    kickoff=KICKOFF,
    source_events={Source.SOURCE_C: "m1", Source.SOURCE_B: "j1", Source.SOURCE_A: "s1"},
    quotes={},
)


def test_exact_tie_prefers_fewer_source_a_match_side_assignments() -> None:
    result = choose_policy(draw_only_opportunity(profit=1000), wide_opportunity(profit=1000))
    assert result.selected.include_source_a_match_sides is False


def test_threshold_does_not_qualify_a_value_that_only_rounds_to_point_95() -> None:
    opportunity = opportunity_with(budget_cents=1_000_000, profit_cents=9_499)
    assert profit_basis_points(opportunity) == 95
    assert meets_threshold(opportunity, 95) is False


def test_threshold_qualifies_at_exactly_95_basis_points() -> None:
    opportunity = opportunity_with(budget_cents=1_000_000, profit_cents=9_500)
    assert meets_threshold(opportunity, 95) is True


def test_uses_source_a_match_sides_detects_home_or_away_source_a_selection() -> None:
    assert uses_source_a_match_sides(wide_opportunity()) is True
    assert uses_source_a_match_sides(draw_only_opportunity()) is False


def test_relevant_sources_include_the_displayed_draw_only_comparison() -> None:
    result = choose_policy(draw_only_opportunity(), wide_opportunity())
    assert result.relevant_sources == frozenset({Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_A})


def test_relevant_sources_exclude_draw_only_when_comparison_is_not_displayed() -> None:
    # Both policies land on the same SOURCE_C/SOURCE_A/SOURCE_B assignment, so the wide policy
    # never actually places Source A on HOME/AWAY -- no comparison block, no extra sources.
    draw = draw_only_opportunity(profit=500)
    wide = draw_only_opportunity(profit=500)
    result = choose_policy(draw, wide)
    assert result.uses_source_a_match_sides is False
    assert result.relevant_sources == frozenset({Source.SOURCE_C, Source.SOURCE_A, Source.SOURCE_B})


def test_evaluate_event_returns_one_evaluation_per_market() -> None:
    def calculator(**kwargs: Any) -> tuple[CalculatedOpportunity, ...]:
        return (
            _opportunity(240, MarketType.FULL_TIME_RESULT),
            _opportunity(110, MarketType.TOTAL_GOALS_25),
        )

    result = evaluate_event_by_market(EVENT, 1_000_000, NOW, 60, calculator=calculator)

    assert set(result) == {MarketType.FULL_TIME_RESULT, MarketType.TOTAL_GOALS_25}
    assert result[MarketType.FULL_TIME_RESULT].market_type is MarketType.FULL_TIME_RESULT


def test_draw_only_and_wide_are_never_paired_across_markets() -> None:
    def calculator(
        *, include_source_a_match_sides: bool, **kwargs: Any
    ) -> tuple[CalculatedOpportunity, ...]:
        if include_source_a_match_sides:
            return (_opportunity(400, MarketType.TOTAL_GOALS_25),)
        return (_opportunity(240, MarketType.FULL_TIME_RESULT),)

    result = evaluate_event_by_market(EVENT, 1_000_000, NOW, 60, calculator=calculator)

    for market_type, evaluated in result.items():
        assert evaluated.selected.market_type is market_type
        assert evaluated.draw_only.market_type is market_type


def test_a_market_present_in_only_one_policy_is_still_evaluated() -> None:
    def calculator(
        *, include_source_a_match_sides: bool, **kwargs: Any
    ) -> tuple[CalculatedOpportunity, ...]:
        if include_source_a_match_sides:
            return (_opportunity(400, MarketType.FULL_TIME_RESULT),)
        return ()

    result = evaluate_event_by_market(EVENT, 1_000_000, NOW, 60, calculator=calculator)

    assert result[MarketType.FULL_TIME_RESULT].uses_source_a_match_sides is False
    assert (
        result[MarketType.FULL_TIME_RESULT].draw_only
        is result[MarketType.FULL_TIME_RESULT].selected
    )


def test_choose_policy_preserves_selected_market_type() -> None:
    opp = _opportunity(200, MarketType.TOTAL_GOALS_25)
    result = choose_policy(opp, opp)
    assert result.market_type is MarketType.TOTAL_GOALS_25

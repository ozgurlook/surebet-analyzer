from __future__ import annotations

"""Strategy engine for mobile surebet UI with per-bet cashback modeling."""

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from surebet.domain.enums import (
    MarketType,
    Outcome,
    QuoteStatus,
    Source,
    Sport,
    TwoWayOutcome,
    operator_group,
)
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.money import payout_cents
from surebet.opportunities.allocation import CASHBACK_SOURCES, optimal_stakes
from surebet.opportunities.allocation_2way import optimal_stakes_2way
from surebet.taxonomy.models import TaxonomyStatus


class StrategySelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Outcome | TwoWayOutcome | StrictStr
    source: Source
    odds: Decimal
    stake_cents: int
    cashback_eligible: StrictBool
    event_id: StrictStr | None = None


class ScenarioBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Outcome | TwoWayOutcome | StrictStr
    winning_source: Source
    winning_odds: Decimal
    winning_stake_cents: int
    gross_payout_cents: int
    losing_eligible_stakes_cents: int
    cashback_cents: int
    total_return_cents: int
    total_stake_cents: int
    standard_profit_cents: int
    net_profit_cents: int
    net_profit_percent: float


class CalculatedOpportunity(BaseModel):
    model_config = ConfigDict(frozen=True)

    opportunity_id: StrictStr
    matched_id: StrictStr
    market_type: MarketType = MarketType.FULL_TIME_RESULT
    market_label: StrictStr = "Maç Sonucu 1X2"
    sport: Sport = Sport.FOOTBALL
    competition: StrictStr
    country_id: StrictStr | None
    country_name: StrictStr | None
    country_iso_code: StrictStr | None
    league_id: StrictStr | None
    league_name: StrictStr | None
    home: StrictStr
    away: StrictStr
    kickoff: datetime
    budget_cents: int
    include_source_a_match_sides: StrictBool
    selections: Mapping[Any, StrategySelection]
    scenarios: Mapping[Any, ScenarioBreakdown]
    min_adjusted_profit_cents: int
    min_adjusted_profit_percent: float
    min_standard_profit_cents: int
    min_standard_profit_percent: float
    is_positive: StrictBool
    oldest_quote_age_seconds: int
    calculated_at: datetime
    alternatives: tuple[CalculatedOpportunity, ...] = ()


def _evaluate_assignment_scenarios(
    quotes: Mapping[Outcome, Quote],
    stakes: Mapping[Outcome, int],
) -> dict[Outcome, ScenarioBreakdown]:
    total_stake = sum(stakes.values())
    scenarios: dict[Outcome, ScenarioBreakdown] = {}

    for winning_outcome in Outcome:
        winning_quote = quotes[winning_outcome]
        winning_stake = stakes[winning_outcome]
        gross_payout = payout_cents(winning_stake, winning_quote.odds)

        # Losing selections
        losing_eligible_stake = sum(
            stakes[losing_outcome]
            for losing_outcome in Outcome
            if losing_outcome != winning_outcome
            and quotes[losing_outcome].source in CASHBACK_SOURCES
        )

        # Direct 10% cash bonus on losing eligible stakes (floor to kuruş)
        cashback = (losing_eligible_stake * 10) // 100
        total_return = gross_payout + cashback
        standard_profit = gross_payout - total_stake
        net_profit = total_return - total_stake
        net_profit_percent = round((net_profit / total_stake) * 100, 2) if total_stake > 0 else 0.0

        scenarios[winning_outcome] = ScenarioBreakdown(
            outcome=winning_outcome,
            winning_source=winning_quote.source,
            winning_odds=winning_quote.odds,
            winning_stake_cents=winning_stake,
            gross_payout_cents=gross_payout,
            losing_eligible_stakes_cents=losing_eligible_stake,
            cashback_cents=cashback,
            total_return_cents=total_return,
            total_stake_cents=total_stake,
            standard_profit_cents=standard_profit,
            net_profit_cents=net_profit,
            net_profit_percent=net_profit_percent,
        )

    return scenarios


def solve_assignment_opportunity(
    quotes: Mapping[Outcome, Quote],
    budget_cents: int,
    now: datetime,
    matched_id: str = "",
    competition: str = "",
    country_id: str | None = None,
    country_name: str | None = None,
    country_iso_code: str | None = None,
    league_id: str | None = None,
    league_name: str | None = None,
    home: str = "",
    away: str = "",
    kickoff: datetime | None = None,
    include_source_a_match_sides: bool = False,
) -> CalculatedOpportunity | None:
    """Solve the max-min kuruş stake allocation for one specific quote assignment."""
    # One kuruş per outcome is the floor the allocation contract requires.
    if budget_cents < len(Outcome):
        return None

    solved_stakes = optimal_stakes(
        {outcome: quotes[outcome].odds for outcome in Outcome},
        {outcome: quotes[outcome].source for outcome in Outcome},
        budget_cents,
    )

    # Evaluate exact scenarios post-solve
    scenarios = _evaluate_assignment_scenarios(quotes, solved_stakes)

    min_adj = min(s.net_profit_cents for s in scenarios.values())
    min_std = min(s.standard_profit_cents for s in scenarios.values())
    min_adj_pct = round((min_adj / budget_cents) * 100, 2)
    min_std_pct = round((min_std / budget_cents) * 100, 2)

    selections: dict[Outcome, StrategySelection] = {
        outcome: StrategySelection(
            outcome=outcome,
            source=quotes[outcome].source,
            odds=quotes[outcome].odds,
            stake_cents=solved_stakes[outcome],
            cashback_eligible=(quotes[outcome].source in CASHBACK_SOURCES),
            event_id=quotes[outcome].event_id,
        )
        for outcome in Outcome
    }

    # Oldest quote age
    now_utc = now.astimezone(UTC)
    oldest_quote_time = min(q.observed_at.astimezone(UTC) for q in quotes.values())
    oldest_age = max(0, int((now_utc - oldest_quote_time).total_seconds()))

    opp_id = f"{matched_id}:{quotes[Outcome.HOME].source.value}:{quotes[Outcome.DRAW].source.value}:{quotes[Outcome.AWAY].source.value}"

    return CalculatedOpportunity(
        opportunity_id=opp_id,
        matched_id=matched_id,
        competition=competition,
        country_id=country_id,
        country_name=country_name,
        country_iso_code=country_iso_code,
        league_id=league_id,
        league_name=league_name,
        home=home,
        away=away,
        kickoff=kickoff or now,
        budget_cents=budget_cents,
        include_source_a_match_sides=include_source_a_match_sides,
        selections=MappingProxyType(selections),
        scenarios=MappingProxyType(scenarios),
        min_adjusted_profit_cents=min_adj,
        min_adjusted_profit_percent=min_adj_pct,
        min_standard_profit_cents=min_std,
        min_standard_profit_percent=min_std_pct,
        is_positive=(min_adj > 0),
        oldest_quote_age_seconds=oldest_age,
        calculated_at=now_utc,
    )


def _evaluate_2way_assignment_scenarios(
    quotes: Mapping[Any, Quote],
    stakes: Mapping[Any, int],
) -> dict[Any, ScenarioBreakdown]:
    total_stake = sum(stakes.values())
    scenarios: dict[Any, ScenarioBreakdown] = {}
    outcomes = tuple(quotes.keys())

    for winning_outcome in outcomes:
        winning_quote = quotes[winning_outcome]
        winning_stake = stakes[winning_outcome]
        gross_payout = payout_cents(winning_stake, winning_quote.odds)

        losing_eligible_stake = sum(
            stakes[losing_outcome]
            for losing_outcome in outcomes
            if losing_outcome != winning_outcome
            and quotes[losing_outcome].source in CASHBACK_SOURCES
        )

        cashback = (losing_eligible_stake * 10) // 100
        total_return = gross_payout + cashback
        standard_profit = gross_payout - total_stake
        net_profit = total_return - total_stake
        net_profit_percent = round((net_profit / total_stake) * 100, 2) if total_stake > 0 else 0.0

        scenarios[winning_outcome] = ScenarioBreakdown(
            outcome=winning_outcome,
            winning_source=winning_quote.source,
            winning_odds=winning_quote.odds,
            winning_stake_cents=winning_stake,
            gross_payout_cents=gross_payout,
            losing_eligible_stakes_cents=losing_eligible_stake,
            cashback_cents=cashback,
            total_return_cents=total_return,
            total_stake_cents=total_stake,
            standard_profit_cents=standard_profit,
            net_profit_cents=net_profit,
            net_profit_percent=net_profit_percent,
        )

    return scenarios


def solve_2way_assignment_opportunity(
    quotes: Mapping[Any, Quote],
    market_type: MarketType,
    market_label: str,
    budget_cents: int,
    now: datetime,
    matched_id: str = "",
    competition: str = "",
    country_id: str | None = None,
    country_name: str | None = None,
    country_iso_code: str | None = None,
    league_id: str | None = None,
    league_name: str | None = None,
    home: str = "",
    away: str = "",
    kickoff: datetime | None = None,
    include_source_a_match_sides: bool = False,
    sport: Sport = Sport.FOOTBALL,
) -> CalculatedOpportunity | None:
    """Solve the max-min kuruş stake allocation for one two-way assignment."""
    if budget_cents < 2:
        return None

    solved_stakes = optimal_stakes_2way(
        {outcome: quotes[outcome].odds for outcome in quotes},
        {outcome: quotes[outcome].source for outcome in quotes},
        budget_cents,
    )

    scenarios = _evaluate_2way_assignment_scenarios(quotes, solved_stakes)

    min_adj = min(s.net_profit_cents for s in scenarios.values())
    min_std = min(s.standard_profit_cents for s in scenarios.values())
    min_adj_pct = round((min_adj / budget_cents) * 100, 2)
    min_std_pct = round((min_std / budget_cents) * 100, 2)

    selections: dict[Any, StrategySelection] = {
        outcome: StrategySelection(
            outcome=outcome,
            source=quotes[outcome].source,
            odds=quotes[outcome].odds,
            stake_cents=solved_stakes[outcome],
            cashback_eligible=(quotes[outcome].source in CASHBACK_SOURCES),
            event_id=quotes[outcome].event_id,
        )
        for outcome in quotes
    }

    now_utc = now.astimezone(UTC)
    oldest_quote_time = min(q.observed_at.astimezone(UTC) for q in quotes.values())
    oldest_age = max(0, int((now_utc - oldest_quote_time).total_seconds()))

    outcomes_list = list(quotes.keys())
    s1 = quotes[outcomes_list[0]].source.value
    s2 = quotes[outcomes_list[1]].source.value
    m_code = market_type.value.lower()
    line_suffix = ""
    for q in quotes.values():
        if q.line is not None:
            line_suffix = f":{q.line}"
            break
    opp_id = f"{matched_id}:{m_code}:{s1}:{s2}{line_suffix}"

    return CalculatedOpportunity(
        opportunity_id=opp_id,
        matched_id=matched_id,
        market_type=market_type,
        market_label=market_label,
        sport=sport,
        competition=competition,
        country_id=country_id,
        country_name=country_name,
        country_iso_code=country_iso_code,
        league_id=league_id,
        league_name=league_name,
        home=home,
        away=away,
        kickoff=kickoff or now,
        budget_cents=budget_cents,
        include_source_a_match_sides=include_source_a_match_sides,
        selections=MappingProxyType(selections),
        scenarios=MappingProxyType(scenarios),
        min_adjusted_profit_cents=min_adj,
        min_adjusted_profit_percent=min_adj_pct,
        min_standard_profit_cents=min_std,
        min_standard_profit_percent=min_std_pct,
        is_positive=(min_adj > 0),
        oldest_quote_age_seconds=oldest_age,
        calculated_at=now_utc,
    )


def calculate_event_opportunities(
    event: MatchedEvent,
    budget_cents: int,
    include_source_a_match_sides: bool,
    now: datetime,
    stale_after_seconds: int = 120,
) -> tuple[CalculatedOpportunity, ...]:
    """Enumerate valid source assignments across 1X2 and 2-way markets and optimize."""
    if event.taxonomy_status is not TaxonomyStatus.VERIFIED:
        return ()

    if event.sport is Sport.BASKETBALL:
        return calculate_basketball_event_opportunities(
            event=event,
            budget_cents=budget_cents,
            include_source_a_match_sides=include_source_a_match_sides,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )

    now_utc = now.astimezone(UTC)
    stale_threshold = timedelta(seconds=stale_after_seconds)

    opportunities: list[CalculatedOpportunity] = []

    # 1. 1X2 (Full Time Result) Market
    source_a_sources = (Source.SOURCE_A, Source.SOURCE_D)
    non_source_a_sources = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_F, Source.SOURCE_E)
    if include_source_a_match_sides:
        allowed_home_sources: tuple[Source, ...] = (*non_source_a_sources, *source_a_sources)
        allowed_away_sources: tuple[Source, ...] = (*non_source_a_sources, *source_a_sources)
    else:
        allowed_home_sources = non_source_a_sources
        allowed_away_sources = non_source_a_sources
    allowed_draw_sources: tuple[Source, ...] = (*non_source_a_sources, *source_a_sources)

    available_quotes: dict[Outcome, list[Quote]] = {outcome: [] for outcome in Outcome}

    for (source, outcome), quote in event.quotes.items():
        if quote.status != QuoteStatus.OPEN:
            continue
        age = now_utc - quote.observed_at.astimezone(UTC)
        if age > stale_threshold:
            continue

        if outcome == Outcome.HOME and source in allowed_home_sources:
            available_quotes[Outcome.HOME].append(quote)
        elif outcome == Outcome.DRAW and source in allowed_draw_sources:
            available_quotes[Outcome.DRAW].append(quote)
        elif outcome == Outcome.AWAY and source in allowed_away_sources:
            available_quotes[Outcome.AWAY].append(quote)

    if all(available_quotes.values()):
        candidates = [
            available_quotes[Outcome.HOME],
            available_quotes[Outcome.DRAW],
            available_quotes[Outcome.AWAY],
        ]

        for selected in product(*candidates):
            sources = {quote.source for quote in selected}
            operator_groups = {operator_group(quote.source) for quote in selected}
            if len(operator_groups) < 2 and (
                len(sources) > 1 or not any(s in CASHBACK_SOURCES for s in sources)
            ):
                continue
            assignment = {
                Outcome.HOME: selected[0],
                Outcome.DRAW: selected[1],
                Outcome.AWAY: selected[2],
            }
            opp = solve_assignment_opportunity(
                quotes=assignment,
                budget_cents=budget_cents,
                now=now,
                matched_id=event.matched_id,
                competition=event.competition,
                country_id=event.country_id,
                country_name=event.country_name,
                country_iso_code=event.country_iso_code,
                league_id=event.league_id,
                league_name=event.league_name,
                home=event.home,
                away=event.away,
                kickoff=event.kickoff,
                include_source_a_match_sides=include_source_a_match_sides,
            )
            if opp is not None:
                opportunities.append(opp)

    # 2. 2-Way Markets (OU25)
    two_way_markets = [
        (
            MarketType.TOTAL_GOALS_25,
            "2.5 Alt / Üst",
            (TwoWayOutcome.OVER, TwoWayOutcome.UNDER),
            event.ou25_quotes,
        ),
    ]

    for m_type, m_label, (o1, o2), market_quotes in two_way_markets:
        if not market_quotes:
            continue

        o1_quotes: list[Quote] = []
        o2_quotes: list[Quote] = []

        for (src, two_way_outcome), quote in market_quotes.items():
            if quote.status != QuoteStatus.OPEN:
                continue
            age = now_utc - quote.observed_at.astimezone(UTC)
            if age > stale_threshold:
                continue
            if two_way_outcome == o1:
                o1_quotes.append(quote)
            elif two_way_outcome == o2:
                o2_quotes.append(quote)

        for q1, q2 in product(o1_quotes, o2_quotes):
            two_way_assignment = {o1: q1, o2: q2}
            opp = solve_2way_assignment_opportunity(
                quotes=two_way_assignment,
                market_type=m_type,
                market_label=m_label,
                budget_cents=budget_cents,
                now=now,
                matched_id=event.matched_id,
                competition=event.competition,
                country_id=event.country_id,
                country_name=event.country_name,
                country_iso_code=event.country_iso_code,
                league_id=event.league_id,
                league_name=event.league_name,
                home=event.home,
                away=event.away,
                kickoff=event.kickoff,
                include_source_a_match_sides=include_source_a_match_sides,
            )
            if opp is not None:
                opportunities.append(opp)

    # Sort descending by minimum adjusted profit
    opportunities.sort(key=lambda o: -o.min_adjusted_profit_cents)
    return tuple(opportunities)


def calculate_basketball_event_opportunities(
    event: MatchedEvent,
    budget_cents: int = 100_000,
    include_source_a_match_sides: bool = True,
    now: datetime | None = None,
    stale_after_seconds: int = 120,
) -> tuple[CalculatedOpportunity, ...]:
    """Calculate arbitrage opportunities for basketball events (Moneyline and Asian Handicap)."""
    if event.taxonomy_status is not TaxonomyStatus.VERIFIED:
        return ()

    now = now or datetime.now(UTC)
    now_utc = now.astimezone(UTC)
    stale_threshold = timedelta(seconds=stale_after_seconds)

    opportunities: list[CalculatedOpportunity] = []

    # 1. Basketball Moneyline (Kim Kazanır, Uzatmalar Dahil)
    ml_home_quotes: list[Quote] = []
    ml_away_quotes: list[Quote] = []

    for (source, outcome), quote in event.bask_ml_quotes.items():
        if quote.status != QuoteStatus.OPEN:
            continue
        if (now_utc - quote.observed_at.astimezone(UTC)) > stale_threshold:
            continue
        if outcome == Outcome.HOME:
            ml_home_quotes.append(quote)
        elif outcome == Outcome.AWAY:
            ml_away_quotes.append(quote)

    for q1, q2 in product(ml_home_quotes, ml_away_quotes):
        sources = {q1.source, q2.source}
        if len({operator_group(s) for s in sources}) < 2:
            continue
        assignment = {Outcome.HOME: q1, Outcome.AWAY: q2}
        opp = solve_2way_assignment_opportunity(
            quotes=assignment,
            market_type=MarketType.BASKETBALL_MONEYLINE,
            market_label="Maç Kazananı (Uzatmalar Dahil)",
            budget_cents=budget_cents,
            now=now,
            matched_id=event.matched_id,
            competition=event.competition,
            country_id=event.country_id,
            country_name=event.country_name,
            country_iso_code=event.country_iso_code,
            league_id=event.league_id,
            league_name=event.league_name,
            home=event.home,
            away=event.away,
            kickoff=event.kickoff,
            include_source_a_match_sides=include_source_a_match_sides,
            sport=event.sport,
        )
        if opp is not None:
            opportunities.append(opp)

    # 2. Basketball Asian Handicap (Asya Handikap, Uzatmalar Dahil)
    # Symmetric line matching: Home line L_h and Away line L_a where L_h + L_a == 0
    ah_home_by_line: dict[Decimal, list[Quote]] = defaultdict(list)
    ah_away_by_line: dict[Decimal, list[Quote]] = defaultdict(list)

    for (source, outcome, line), quote in event.bask_ah_quotes.items():
        if quote.status != QuoteStatus.OPEN:
            continue
        if (now_utc - quote.observed_at.astimezone(UTC)) > stale_threshold:
            continue
        if line is None:
            continue
        if outcome == Outcome.HOME:
            ah_home_by_line[line].append(quote)
        elif outcome == Outcome.AWAY:
            ah_away_by_line[line].append(quote)

    for home_line, h_quotes in ah_home_by_line.items():
        opp_away_line = -home_line
        if opp_away_line not in ah_away_by_line:
            continue
        a_quotes = ah_away_by_line[opp_away_line]

        h_fmt = f"{'+' if home_line > 0 else ''}{home_line}"
        a_fmt = f"{'+' if opp_away_line > 0 else ''}{opp_away_line}"
        market_label = f"Asya Handikap ({h_fmt} / {a_fmt})"

        for q1, q2 in product(h_quotes, a_quotes):
            sources = {q1.source, q2.source}
            if len({operator_group(s) for s in sources}) < 2:
                continue
            assignment = {Outcome.HOME: q1, Outcome.AWAY: q2}
            opp = solve_2way_assignment_opportunity(
                quotes=assignment,
                market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
                market_label=market_label,
                budget_cents=budget_cents,
                now=now,
                matched_id=event.matched_id,
                competition=event.competition,
                country_id=event.country_id,
                country_name=event.country_name,
                country_iso_code=event.country_iso_code,
                league_id=event.league_id,
                league_name=event.league_name,
                home=event.home,
                away=event.away,
                kickoff=event.kickoff,
                include_source_a_match_sides=include_source_a_match_sides,
                sport=event.sport,
            )
            if opp is not None:
                opportunities.append(opp)

    opportunities.sort(key=lambda o: -o.min_adjusted_profit_cents)
    return tuple(opportunities)


CalculatedOpportunity.model_rebuild()

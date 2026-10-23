from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from surebet.domain.enums import MarketType, Outcome, QuoteStatus, Source, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.opportunities.strategy import (
    calculate_event_opportunities,
    solve_2way_assignment_opportunity,
)
from surebet.taxonomy.models import TaxonomyStatus

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _make_quote(
    source: Source,
    outcome: TwoWayOutcome | Outcome,
    odds: str,
    market_type: MarketType,
) -> Quote:
    return Quote(
        source=source,
        event_id="evt-1",
        outcome=outcome,
        odds=Decimal(odds),
        observed_at=NOW,
        status=QuoteStatus.OPEN,
        market_type=market_type,
    )


def test_solve_2way_source_c_and_source_b_cashback_surebet() -> None:
    quotes = {
        TwoWayOutcome.OVER: _make_quote(
            Source.SOURCE_C, TwoWayOutcome.OVER, "1.95", MarketType.TOTAL_GOALS_25
        ),
        TwoWayOutcome.UNDER: _make_quote(
            Source.SOURCE_B, TwoWayOutcome.UNDER, "1.95", MarketType.TOTAL_GOALS_25
        ),
    }

    opp = solve_2way_assignment_opportunity(
        quotes=quotes,
        market_type=MarketType.TOTAL_GOALS_25,
        market_label="2.5 Alt / Üst",
        budget_cents=100_000,
        now=NOW,
        matched_id="match-1",
        competition="Premier League",
        home="Arsenal",
        away="Chelsea",
    )

    assert opp is not None
    assert opp.market_type is MarketType.TOTAL_GOALS_25
    assert opp.market_label == "2.5 Alt / Üst"
    assert opp.is_positive is True
    assert opp.min_adjusted_profit_percent == 2.50
    assert opp.min_adjusted_profit_cents == 2_500
    assert opp.selections[TwoWayOutcome.OVER].cashback_eligible is True
    assert opp.selections[TwoWayOutcome.OVER].event_id == "evt-1"
    assert opp.selections[TwoWayOutcome.UNDER].cashback_eligible is True
    assert opp.selections[TwoWayOutcome.UNDER].event_id == "evt-1"


def test_solve_2way_source_c_and_source_c_same_source_supported() -> None:
    quotes = {
        TwoWayOutcome.OVER: _make_quote(
            Source.SOURCE_C, TwoWayOutcome.OVER, "1.95", MarketType.TOTAL_GOALS_25
        ),
        TwoWayOutcome.UNDER: _make_quote(
            Source.SOURCE_C, TwoWayOutcome.UNDER, "1.95", MarketType.TOTAL_GOALS_25
        ),
    }

    opp = solve_2way_assignment_opportunity(
        quotes=quotes,
        market_type=MarketType.TOTAL_GOALS_25,
        market_label="2.5 Alt / Üst",
        budget_cents=100_000,
        now=NOW,
        matched_id="match-1",
    )

    assert opp is not None
    assert opp.is_positive is True
    assert opp.min_adjusted_profit_percent == 2.50


def test_calculate_event_opportunities_includes_2way_markets() -> None:
    # 1X2 quotes (sub-zero margin, no surebet)
    quotes_1x2 = {
        (Source.SOURCE_C, Outcome.HOME): Quote(
            source=Source.SOURCE_C,
            event_id="e1",
            outcome=Outcome.HOME,
            odds=Decimal("2.0"),
            observed_at=NOW,
        ),
        (Source.SOURCE_C, Outcome.DRAW): Quote(
            source=Source.SOURCE_C,
            event_id="e1",
            outcome=Outcome.DRAW,
            odds=Decimal("3.0"),
            observed_at=NOW,
        ),
        (Source.SOURCE_C, Outcome.AWAY): Quote(
            source=Source.SOURCE_C,
            event_id="e1",
            outcome=Outcome.AWAY,
            odds=Decimal("3.5"),
            observed_at=NOW,
        ),
    }
    # OU25 quotes between SOURCE_C and SOURCE_B (surebet with 10% KB)
    ou25_quotes = {
        (Source.SOURCE_C, TwoWayOutcome.OVER): _make_quote(
            Source.SOURCE_C, TwoWayOutcome.OVER, "1.95", MarketType.TOTAL_GOALS_25
        ),
        (Source.SOURCE_B, TwoWayOutcome.UNDER): _make_quote(
            Source.SOURCE_B, TwoWayOutcome.UNDER, "1.95", MarketType.TOTAL_GOALS_25
        ),
    }

    event = MatchedEvent(
        matched_id="m1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="La Liga",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id=None,
        league_name=None,
        home="Real Madrid",
        away="Barcelona",
        kickoff=NOW,
        source_events=MappingProxyType(
            {Source.SOURCE_C: "e1", Source.SOURCE_B: "e2", Source.SOURCE_A: "e3"}
        ),
        quotes=quotes_1x2,
        ou25_quotes=ou25_quotes,
    )

    opps = calculate_event_opportunities(
        event=event,
        budget_cents=100_000,
        include_source_a_match_sides=True,
        now=NOW,
    )

    # Should produce opportunities containing OU25
    market_types = {opp.market_type for opp in opps}
    assert MarketType.TOTAL_GOALS_25 in market_types

    # The positive 2-way opportunities should rank at the top
    assert opps[0].is_positive is True

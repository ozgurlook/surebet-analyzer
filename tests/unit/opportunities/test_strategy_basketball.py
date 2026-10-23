from datetime import UTC, datetime
from decimal import Decimal

from surebet.domain.enums import MarketType, Outcome, QuoteStatus, Source, Sport
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.opportunities.strategy import calculate_basketball_event_opportunities
from surebet.taxonomy.models import TaxonomyStatus

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)


def _make_bask_quote(
    source: Source,
    outcome: Outcome,
    odds: str,
    market_type: MarketType,
    line: Decimal | None = None,
) -> Quote:
    return Quote(
        source=source,
        event_id="evt-1",
        outcome=outcome,
        odds=Decimal(odds),
        observed_at=NOW,
        status=QuoteStatus.OPEN,
        market_type=market_type,
        line=line,
    )


def test_basketball_moneyline_with_source_b_cashback() -> None:
    event = MatchedEvent(
        matched_id="bask-1",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Euroleague",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id="bask-euroleague",
        league_name="Euroleague",
        home="Fenerbahçe",
        away="Panathinaikos",
        kickoff=NOW,
        source_events={Source.SOURCE_A: "s1", Source.SOURCE_B: "j1"},
        quotes={},
        sport=Sport.BASKETBALL,
        bask_ml_quotes={
            (Source.SOURCE_A, Outcome.HOME): _make_bask_quote(
                Source.SOURCE_A, Outcome.HOME, "2.10", MarketType.BASKETBALL_MONEYLINE
            ),
            (Source.SOURCE_B, Outcome.AWAY): _make_bask_quote(
                Source.SOURCE_B, Outcome.AWAY, "1.85", MarketType.BASKETBALL_MONEYLINE
            ),
        },
    )

    opps = calculate_basketball_event_opportunities(event, budget_cents=100_000, now=NOW)
    assert len(opps) >= 1
    best = opps[0]
    assert best.market_type is MarketType.BASKETBALL_MONEYLINE
    assert best.is_positive is True
    assert best.selections[Outcome.AWAY].cashback_eligible is True


def test_basketball_asian_handicap_symmetric_matching() -> None:
    line_home = Decimal("-4.5")
    line_away = Decimal("4.5")
    event = MatchedEvent(
        matched_id="bask-2",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Euroleague",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id="bask-euroleague",
        league_name="Euroleague",
        home="Real Madrid",
        away="Barcelona",
        kickoff=NOW,
        source_events={Source.SOURCE_A: "s1", Source.SOURCE_B: "j1"},
        quotes={},
        sport=Sport.BASKETBALL,
        bask_ah_quotes={
            (Source.SOURCE_A, Outcome.HOME, line_home): _make_bask_quote(
                Source.SOURCE_A,
                Outcome.HOME,
                "1.98",
                MarketType.BASKETBALL_ASIAN_HANDICAP,
                line=line_home,
            ),
            (Source.SOURCE_B, Outcome.AWAY, line_away): _make_bask_quote(
                Source.SOURCE_B,
                Outcome.AWAY,
                "1.92",
                MarketType.BASKETBALL_ASIAN_HANDICAP,
                line=line_away,
            ),
        },
    )

    opps = calculate_basketball_event_opportunities(event, budget_cents=100_000, now=NOW)
    assert len(opps) >= 1
    ah_opp = next(o for o in opps if o.market_type is MarketType.BASKETBALL_ASIAN_HANDICAP)
    assert "4.5" in ah_opp.market_label
    assert ah_opp.is_positive is True


def test_basketball_asian_handicap_rejects_asymmetric_lines() -> None:
    line_home = Decimal("-4.5")
    line_away = Decimal("3.5")  # Not symmetric!
    event = MatchedEvent(
        matched_id="bask-3",
        taxonomy_status=TaxonomyStatus.VERIFIED,
        competition="Euroleague",
        country_id=None,
        country_name=None,
        country_iso_code=None,
        league_id="bask-euroleague",
        league_name="Euroleague",
        home="Real Madrid",
        away="Barcelona",
        kickoff=NOW,
        source_events={Source.SOURCE_A: "s1", Source.SOURCE_B: "j1"},
        quotes={},
        sport=Sport.BASKETBALL,
        bask_ah_quotes={
            (Source.SOURCE_A, Outcome.HOME, line_home): _make_bask_quote(
                Source.SOURCE_A,
                Outcome.HOME,
                "1.98",
                MarketType.BASKETBALL_ASIAN_HANDICAP,
                line=line_home,
            ),
            (Source.SOURCE_B, Outcome.AWAY, line_away): _make_bask_quote(
                Source.SOURCE_B,
                Outcome.AWAY,
                "1.92",
                MarketType.BASKETBALL_ASIAN_HANDICAP,
                line=line_away,
            ),
        },
    )

    opps = calculate_basketball_event_opportunities(event, budget_cents=100_000, now=NOW)
    assert len(opps) == 0

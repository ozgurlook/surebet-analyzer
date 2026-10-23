from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from surebet.domain.enums import MarketType, Outcome, Source, Sport
from surebet.domain.models import Quote
from surebet.matching.matcher import match_events
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.sources.source_a.protocol import parse_basketball_collection
from surebet.sources.wamp_feed.parser import parse_live_records
from surebet.taxonomy.models import (
    CanonicalLeague,
    CanonicalRegion,
    CompetitionKind,
    MappingMethod,
    RegionClass,
    ResolvedCompetition,
    SourceCompetitionEvidence,
    SourceIdentityKind,
)

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)


def test_collected_event_supports_basketball() -> None:
    q_home = Quote(
        source=Source.SOURCE_B,
        event_id="j-100",
        outcome=Outcome.HOME,
        odds=Decimal("1.85"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )
    q_away = Quote(
        source=Source.SOURCE_B,
        event_id="j-100",
        outcome=Outcome.AWAY,
        odds=Decimal("2.05"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )

    event = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="j-100",
        competition="Euroleague",
        home="Fenerbahçe",
        away="Panathinaikos",
        kickoff=KICKOFF,
        sport=Sport.BASKETBALL,
        market_key="basketball.euroleague",
        quotes={},
        bask_ml_quotes={Outcome.HOME: q_home, Outcome.AWAY: q_away},
    )

    assert event.sport is Sport.BASKETBALL
    assert event.bask_ml_quotes is not None
    assert event.bask_ml_quotes[Outcome.HOME].odds == Decimal("1.85")


def test_collected_event_basketball_rejects_empty_markets() -> None:
    with pytest.raises(
        ValidationError, match="basketball event must contain bask_ml_quotes or bask_ah_quotes"
    ):
        CollectedEvent(
            source=Source.SOURCE_B,
            source_event_id="j-100",
            competition="Euroleague",
            home="Fenerbahçe",
            away="Panathinaikos",
            kickoff=KICKOFF,
            sport=Sport.BASKETBALL,
            market_key="basketball.euroleague",
            quotes={},
        )


def test_matcher_aggregates_basketball_events() -> None:
    evidence_source_b = SourceCompetitionEvidence(
        provider="source_b",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="910000000000000111",
        competition_name="Euroleague",
    )
    evidence_source_a = SourceCompetitionEvidence(
        provider="source_a",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="7111",
        competition_name="Avrupa Ligi",
    )

    q_source_b_home = Quote(
        source=Source.SOURCE_B,
        event_id="j-1",
        outcome=Outcome.HOME,
        odds=Decimal("1.85"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )
    q_source_b_away = Quote(
        source=Source.SOURCE_B,
        event_id="j-1",
        outcome=Outcome.AWAY,
        odds=Decimal("2.05"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )

    q_source_a_home = Quote(
        source=Source.SOURCE_A,
        event_id="s-1",
        outcome=Outcome.HOME,
        odds=Decimal("2.10"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )
    q_source_a_away = Quote(
        source=Source.SOURCE_A,
        event_id="s-1",
        outcome=Outcome.AWAY,
        odds=Decimal("1.78"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_MONEYLINE,
    )

    line_home = Decimal("-4.5")
    line_away = Decimal("4.5")
    q_source_b_ah_away = Quote(
        source=Source.SOURCE_B,
        event_id="j-1",
        outcome=Outcome.AWAY,
        odds=Decimal("1.92"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
        line=line_away,
    )
    q_source_a_ah_home = Quote(
        source=Source.SOURCE_A,
        event_id="s-1",
        outcome=Outcome.HOME,
        odds=Decimal("1.95"),
        observed_at=NOW,
        market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
        line=line_home,
    )

    ev_source_b = CollectedEvent(
        source=Source.SOURCE_B,
        source_event_id="j-1",
        competition="Euroleague",
        home="Fenerbahce Beko",
        away="Panathinaikos BC",
        kickoff=KICKOFF,
        sport=Sport.BASKETBALL,
        market_key="basketball.euroleague",
        quotes={},
        bask_ml_quotes={Outcome.HOME: q_source_b_home, Outcome.AWAY: q_source_b_away},
        bask_ah_quotes={(Outcome.AWAY, line_away): q_source_b_ah_away},
        source_competition=evidence_source_b,
    )

    ev_source_a = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="s-1",
        competition="Avrupa Ligi",
        home="Fenerbahçe",
        away="Panathinaikos",
        kickoff=KICKOFF,
        sport=Sport.BASKETBALL,
        market_key="basketball.euroleague",
        quotes={},
        bask_ml_quotes={Outcome.HOME: q_source_a_home, Outcome.AWAY: q_source_a_away},
        bask_ah_quotes={(Outcome.HOME, line_home): q_source_a_ah_home},
        source_competition=evidence_source_a,
    )

    batch_source_b = CollectionBatch(
        source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(ev_source_b,)
    )
    batch_source_a = CollectionBatch(
        source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(ev_source_a,)
    )

    region = CanonicalRegion(
        region_id="region:international",
        region_class=RegionClass.INTERNATIONAL,
        iso_code=None,
        display_name="Europe",
        normalized_key="europe",
    )
    league = CanonicalLeague(
        league_id="league:00000000-0000-4000-8000-000000000099",
        region_id=region.region_id,
        display_name="Euroleague",
        normalized_key="euroleague",
        kind=CompetitionKind.INTERNATIONAL_CLUB,
    )
    resolved_source_b = ResolvedCompetition(
        source=Source.SOURCE_B,
        competition_id="910000000000000111",
        region=region,
        league=league,
        method=MappingMethod.MANUAL,
        confidence=1.0,
    )
    resolved_source_a = ResolvedCompetition(
        source=Source.SOURCE_A,
        competition_id="7111",
        region=region,
        league=league,
        method=MappingMethod.MANUAL,
        confidence=1.0,
    )
    taxonomy_index = {
        (Source.SOURCE_B, "910000000000000111"): resolved_source_b,
        (Source.SOURCE_A, "7111"): resolved_source_a,
    }

    matched = match_events([batch_source_b, batch_source_a], taxonomy_index)
    assert len(matched) == 1
    m = matched[0]
    assert m.sport is Sport.BASKETBALL
    assert m.league_id == "league:00000000-0000-4000-8000-000000000099"
    assert (Source.SOURCE_B, Outcome.HOME) in m.bask_ml_quotes
    assert (Source.SOURCE_A, Outcome.HOME) in m.bask_ml_quotes
    assert (Source.SOURCE_B, Outcome.AWAY, line_away) in m.bask_ah_quotes
    assert (Source.SOURCE_A, Outcome.HOME, line_home) in m.bask_ah_quotes


def test_wamp_feed_parses_basketball_records() -> None:
    records = [
        {
            "_type": "MATCH",
            "id": "1001",
            "sportId": "8",
            "sportName": "Basketbol",
            "homeParticipantId": "p1",
            "awayParticipantId": "p2",
            "homeParticipantName": "Real Madrid",
            "awayParticipantName": "Barcelona",
            "startTime": 1790107200000,
            "statusName": "NotStarted",
            "parentId": "910000000000000111",
            "parentName": "Euroleague",
            "categoryName": "Avrupa",
        },
        # Moneyline market
        {
            "_type": "MARKET",
            "id": "m1",
            "eventId": "1001",
            "name": "Kim Kazanır, Tüm Maç",
            "isAvailable": True,
            "isClosed": False,
        },
        {"_type": "MARKET_OUTCOME_RELATION", "marketId": "m1", "outcomeId": "o1"},
        {"_type": "MARKET_OUTCOME_RELATION", "marketId": "m1", "outcomeId": "o2"},
        {
            "_type": "OUTCOME",
            "id": "o1",
            "paramParticipantId1": "p1",
            "headerName": "Ev Sahibi",
        },
        {
            "_type": "OUTCOME",
            "id": "o2",
            "paramParticipantId1": "p2",
            "headerName": "Deplasman",
        },
        {
            "_type": "BETTING_OFFER",
            "outcomeId": "o1",
            "odds": "1.75",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "outcomeId": "o2",
            "odds": "2.15",
            "isAvailable": True,
            "isLive": False,
        },
        # Asian Handicap market -4.5 / +4.5
        {
            "_type": "MARKET",
            "id": "m2",
            "eventId": "1001",
            "name": "Asya Handikapı -4.5, Tüm Maç",
            "isAvailable": True,
            "isClosed": False,
        },
        {"_type": "MARKET_OUTCOME_RELATION", "marketId": "m2", "outcomeId": "o3"},
        {"_type": "MARKET_OUTCOME_RELATION", "marketId": "m2", "outcomeId": "o4"},
        {
            "_type": "OUTCOME",
            "id": "o3",
            "paramParticipantId1": "p1",
            "headerName": "Ev Sahibi",
        },
        {
            "_type": "OUTCOME",
            "id": "o4",
            "paramParticipantId1": "p2",
            "headerName": "Deplasman",
        },
        {
            "_type": "BETTING_OFFER",
            "outcomeId": "o3",
            "odds": "1.90",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "outcomeId": "o4",
            "odds": "1.90",
            "isAvailable": True,
            "isLive": False,
        },
    ]

    events = parse_live_records(records, source=Source.SOURCE_B, observed_at=NOW)
    assert len(events) == 1
    event = events[0]
    assert event.sport is Sport.BASKETBALL
    assert event.home == "Real Madrid"
    assert event.away == "Barcelona"
    assert event.bask_ml_quotes is not None
    assert event.bask_ml_quotes[Outcome.HOME].odds == Decimal("1.7500")
    assert event.bask_ml_quotes[Outcome.AWAY].odds == Decimal("2.1500")
    assert event.bask_ah_quotes is not None
    assert (Outcome.HOME, Decimal("-4.5")) in event.bask_ah_quotes
    assert (Outcome.AWAY, Decimal("4.5")) in event.bask_ah_quotes


def test_source_a_parses_basketball_rpc_data() -> None:
    games_data = {
        "data": {
            "game": {
                "2001": {
                    "id": 2001,
                    "team1_name": "Real Madrid",
                    "team2_name": "Barcelona",
                    "start_ts": 1790107200,
                    "is_blocked": 0,
                    "sport_alias": "Basketball",
                    "competition": {"id": 7111, "name": "Avrupa Ligi"},
                }
            }
        }
    }
    markets_data = {
        "data": {
            "game": {
                "2001": {
                    "market": {
                        "m_ml": {
                            "id": "m_ml",
                            "type": "P1P2",
                            "name": "Maç Kazananı",
                            "display_key": "WINNER",
                            "event": {
                                "e1": {"id": "e1", "type_1": "W1", "price": "1.72"},
                                "e2": {"id": "e2", "type_1": "W2", "price": "2.20"},
                            },
                        },
                        "m_ah": {
                            "id": "m_ah",
                            "type": "MatchHandicap",
                            "name": "Maç Handikapı",
                            "display_key": "HANDICAP",
                            "event": {
                                "e3": {
                                    "id": "e3",
                                    "type_1": "Handicap1",
                                    "price": "1.88",
                                    "base": "-4.5",
                                },
                                "e4": {
                                    "id": "e4",
                                    "type_1": "Handicap2",
                                    "price": "1.94",
                                    "base": "4.5",
                                },
                            },
                        },
                    }
                }
            }
        }
    }

    events = parse_basketball_collection(games_data, markets_data, observed_at=NOW)
    assert len(events) == 1
    event = events[0]
    assert event.sport is Sport.BASKETBALL
    assert event.home == "Real Madrid"
    assert event.away == "Barcelona"
    assert event.bask_ml_quotes is not None
    assert event.bask_ml_quotes[Outcome.HOME].odds == Decimal("1.7200")
    assert event.bask_ml_quotes[Outcome.AWAY].odds == Decimal("2.2000")
    assert event.bask_ah_quotes is not None
    assert (Outcome.HOME, Decimal("-4.5")) in event.bask_ah_quotes
    assert (Outcome.AWAY, Decimal("4.5")) in event.bask_ah_quotes

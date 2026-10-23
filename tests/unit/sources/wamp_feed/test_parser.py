import json
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from surebet.domain.enums import MarketType, Outcome, Source, TwoWayOutcome
from surebet.sources.models import FULL_TIME_RESULT_MARKET
from surebet.sources.wamp_feed import parser as wamp_parser
from surebet.sources.wamp_feed.parser import (
    parse_envelope,
    parse_live_records,
    parse_records,
    parse_v2_records,
)
from surebet.sources.wamp_feed.protocol import WampFeedProtocolError
from surebet.taxonomy.models import CompetitionKind, SourceIdentityKind

FIXTURES = Path("tests/fixtures/sources/wamp_feed")
OBSERVED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


def load_live_records(name: str) -> tuple[object, ...]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"), parse_float=Decimal)
    assert isinstance(payload, list)
    return tuple(payload)


def load_v2_records(name: str) -> tuple[object, ...]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"), parse_float=Decimal)
    assert isinstance(payload, list)
    return tuple(payload)


def test_parse_v2_records_matches_equivalent_legacy_envelope() -> None:
    legacy_envelope = parse_envelope(
        {
            "format": "json",
            "messageType": "RESULT",
            "version": "1",
            "records": [
                {
                    "_type": "tournament",
                    "id": "source_c-tournament",
                    "sport": "football",
                    "name": "Sample League",
                },
                {
                    "_type": "match",
                    "id": "source_c-future",
                    "tournamentId": "source_c-tournament",
                    "sport": "football",
                    "home": "North City",
                    "away": "South United",
                    "kickoff": "2026-08-25T19:00:00Z",
                    "status": "prematch",
                },
                {
                    "_type": "market",
                    "id": "source_c-future-ftr",
                    "matchId": "source_c-future",
                    "name": "Full Time Result",
                    "status": "open",
                },
                {
                    "_type": "bettingOffer",
                    "id": "source_c-future-offer",
                    "marketId": "source_c-future-ftr",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "source_c-future-home",
                    "bettingOfferId": "source_c-future-offer",
                    "label": "1",
                    "odds": "2.10",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "source_c-future-draw",
                    "bettingOfferId": "source_c-future-offer",
                    "label": "X",
                    "odds": "3.30",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "source_c-future-away",
                    "bettingOfferId": "source_c-future-offer",
                    "label": "2",
                    "odds": "3.60",
                    "status": "open",
                },
            ],
        }
    )

    old_events = parse_records((legacy_envelope,), source=Source.SOURCE_C, observed_at=OBSERVED_AT)
    v2_events = parse_v2_records(
        load_v2_records("source_c_football_1x2_v2.json"),
        source=Source.SOURCE_C,
        observed_at=OBSERVED_AT,
    )

    assert v2_events == old_events


def test_parse_v2_records_normalizes_entity_types_and_explicit_relation_aliases() -> None:
    events = parse_v2_records(
        load_v2_records("source_c_football_1x2_v2.json"),
        source=Source.SOURCE_C,
        observed_at=OBSERVED_AT,
    )

    assert [event.source_event_id for event in events] == ["source_c-future"]
    assert events[0].quotes[Outcome.HOME].odds == Decimal("2.10")


def test_parse_v2_records_omits_only_events_with_missing_canonical_fields() -> None:
    records = load_v2_records("source_c_football_1x2_v2.json") + (
        {
            "entityType": "match",
            "id": "missing-home",
            "tournamentId": "source_c-tournament",
            "sport": "football",
            "away": "Away",
            "kickoff": "2026-08-25T19:00:00Z",
            "status": "prematch",
        },
    )
    events = parse_v2_records(
        records,
        source=Source.SOURCE_C,
        observed_at=OBSERVED_AT,
    )

    assert [event.source_event_id for event in events] == ["source_c-future"]


def test_parse_v2_records_isolates_malformed_records_from_a_valid_graph() -> None:
    """Letting one malformed v2 child reach a global index hides unrelated events."""
    records = load_v2_records("source_c_football_1x2_v2.json") + (
        {"entityType": "Tournament", "id": object(), "sport": "football", "name": "Broken"},
        {
            "entityType": "Match",
            "id": "broken-match",
            "tournamentID": [],
            "sport": "football",
            "home": "Broken",
            "away": "Broken",
            "kickoff": "2026-08-25T19:00:00Z",
            "status": "prematch",
        },
        {
            "entityType": "Market",
            "id": "broken-market",
            "matchID": "broken-match",
            "name": 1,
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "broken-odds",
            "bettingOfferID": "broken-offer",
            "label": "1",
            "odds": "not-odds",
            "status": "open",
        },
        {
            "entityType": "Match",
            "id": "broken-kickoff",
            "tournamentID": "league",
            "sport": "football",
            "home": "Broken",
            "away": "Broken",
            "kickoff": "not-a-date",
            "status": "prematch",
        },
        {
            "entityType": "Outcome",
            "id": "duplicate-child",
            "bettingOfferID": "broken-offer",
            "label": "1",
            "odds": "2.00",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "duplicate-child",
            "bettingOfferID": "broken-offer",
            "label": "2",
            "odds": "4.00",
            "status": "open",
        },
    )

    events = parse_v2_records(records, source=Source.SOURCE_C, observed_at=OBSERVED_AT)

    assert [event.source_event_id for event in events] == ["source_c-future"]


def test_parse_v2_records_rejects_malformed_protocol_level_records() -> None:
    with pytest.raises(WampFeedProtocolError, match="v2 records"):
        parse_v2_records(("not-a-record",), source=Source.SOURCE_C, observed_at=OBSERVED_AT)


def test_parse_live_records_normalizes_current_wamp_feed_aggregator_graph() -> None:
    observed_at = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
    records = (
        {
            "_type": "MATCH",
            "id": "match-1",
            "sportId": "1",
            "sportName": "Futbol",
            "parentId": "tournament-44",
            "parentName": "Sample Premier League 2026/2027",
            "categoryId": "category-pt",
            "categoryName": "Portekiz",
            "homeParticipantId": "team-home",
            "awayParticipantId": "team-away",
            "homeParticipantName": "North City",
            "awayParticipantName": "South United",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        {
            "_type": "MARKET",
            "id": "market-1",
            "eventId": "match-1",
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-home",
            "marketId": "market-1",
            "outcomeId": "out-home",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-draw",
            "marketId": "market-1",
            "outcomeId": "out-draw",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-away",
            "marketId": "market-1",
            "outcomeId": "out-away",
        },
        {
            "_type": "OUTCOME",
            "id": "out-home",
            "eventId": "match-1",
            "code": "#Pteam-home",
            "paramParticipantId1": "team-home",
            "translatedName": "North City",
        },
        {
            "_type": "OUTCOME",
            "id": "out-draw",
            "eventId": "match-1",
            "code": "#D",
            "translatedName": "Berabere",
        },
        {
            "_type": "OUTCOME",
            "id": "out-away",
            "eventId": "match-1",
            "code": "#Pteam-away",
            "paramParticipantId1": "team-away",
            "translatedName": "South United",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-home",
            "outcomeId": "out-home",
            "odds": "2.10",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-draw",
            "outcomeId": "out-draw",
            "odds": "3.30",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-away",
            "outcomeId": "out-away",
            "odds": "3.60",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
    )

    events = wamp_parser.parse_live_records(
        records, source=Source.SOURCE_C, observed_at=observed_at
    )

    assert [event.source_event_id for event in events] == ["match-1"]
    event = events[0]
    assert event.competition == "Sample Premier League 2026/2027"
    assert event.home == "North City"
    assert event.away == "South United"
    assert event.quotes[Outcome.HOME].odds == Decimal("2.10")
    assert event.quotes[Outcome.DRAW].odds == Decimal("3.30")
    assert event.quotes[Outcome.AWAY].odds == Decimal("3.60")
    evidence = event.source_competition
    assert evidence is not None
    assert evidence.provider == "source_c"
    assert evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
    assert evidence.competition_id == "tournament-44"
    assert evidence.competition_name == "Sample Premier League 2026/2027"
    assert evidence.country_id == "category-pt"
    assert evidence.country_name == "Portekiz"
    assert evidence.kind is CompetitionKind.UNKNOWN


def test_parse_live_records_still_accepts_markets_with_no_betting_type_id() -> None:
    """Source C/Source B markets never carry bettingTypeId; the early-settlement exclusion
    added for Source E must be a no-op for them, not a new required field."""
    observed_at = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
    records = (
        {
            "_type": "MATCH",
            "id": "match-1",
            "sportId": "1",
            "sportName": "Futbol",
            "parentId": "tournament-44",
            "parentName": "Sample Premier League 2026/2027",
            "categoryId": "category-pt",
            "categoryName": "Portekiz",
            "homeParticipantId": "team-home",
            "awayParticipantId": "team-away",
            "homeParticipantName": "North City",
            "awayParticipantName": "South United",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        {
            "_type": "MARKET",
            "id": "market-1",
            "eventId": "match-1",
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-home",
            "marketId": "market-1",
            "outcomeId": "out-home",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-draw",
            "marketId": "market-1",
            "outcomeId": "out-draw",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-away",
            "marketId": "market-1",
            "outcomeId": "out-away",
        },
        {
            "_type": "OUTCOME",
            "id": "out-home",
            "eventId": "match-1",
            "code": "#Pteam-home",
            "paramParticipantId1": "team-home",
            "translatedName": "North City",
        },
        {
            "_type": "OUTCOME",
            "id": "out-draw",
            "eventId": "match-1",
            "code": "#D",
            "translatedName": "Berabere",
        },
        {
            "_type": "OUTCOME",
            "id": "out-away",
            "eventId": "match-1",
            "code": "#Pteam-away",
            "paramParticipantId1": "team-away",
            "translatedName": "South United",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-home",
            "outcomeId": "out-home",
            "odds": "2.10",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-draw",
            "outcomeId": "out-draw",
            "odds": "3.30",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-away",
            "outcomeId": "out-away",
            "odds": "3.60",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
    )

    events = wamp_parser.parse_live_records(
        records, source=Source.SOURCE_C, observed_at=observed_at
    )

    assert [event.source_event_id for event in events] == ["match-1"]
    assert events[0].quotes[Outcome.DRAW].odds == Decimal("3.30")


def test_parse_live_records_excludes_the_early_settlement_variant_of_full_time_result() -> None:
    """Source E publishes two markets named identically ('Maç Sonucu, Normal Süre')
    for the same match: bettingTypeId 69 ('1x2', wanted) and bettingTypeId 1510
    ('Kim Kazanır', early-settlement, unwanted). Without this exclusion both are
    accepted as candidates and the match is dropped entirely (parse_live_records only
    keeps a match with exactly one Full Time Result candidate market)."""
    match = {
        "_type": "MATCH",
        "id": "match-1",
        "sportId": "1",
        "sportName": "Futbol",
        "parentId": "tournament-44",
        "parentName": "Sample Premier League 2026/2027",
        "categoryId": "category-pt",
        "categoryName": "Portekiz",
        "homeParticipantId": "team-home",
        "awayParticipantId": "team-away",
        "homeParticipantName": "North City",
        "awayParticipantName": "South United",
        "startTime": 1787684400000,
        "statusName": "Beklemede",
    }
    standard_market = {
        "_type": "MARKET",
        "id": "market-standard",
        "eventId": "match-1",
        "name": "Maç Sonucu, Normal Süre",
        "bettingTypeId": "69",
        "bettingTypeName": "1x2",
        "isAvailable": True,
        "isClosed": False,
    }
    early_settlement_market = {
        "_type": "MARKET",
        "id": "market-early-settlement",
        "eventId": "match-1",
        "name": "Maç Sonucu, Normal Süre",
        "bettingTypeId": "1510",
        "bettingTypeName": "Kim Kazanır",
        "isAvailable": True,
        "isClosed": False,
    }

    def outcome_records(market_id: str, suffix: str) -> tuple[dict, ...]:
        return (
            {
                "_type": "MARKET_OUTCOME_RELATION",
                "id": f"rel-home-{suffix}",
                "marketId": market_id,
                "outcomeId": f"out-home-{suffix}",
            },
            {
                "_type": "MARKET_OUTCOME_RELATION",
                "id": f"rel-draw-{suffix}",
                "marketId": market_id,
                "outcomeId": f"out-draw-{suffix}",
            },
            {
                "_type": "MARKET_OUTCOME_RELATION",
                "id": f"rel-away-{suffix}",
                "marketId": market_id,
                "outcomeId": f"out-away-{suffix}",
            },
            {
                "_type": "OUTCOME",
                "id": f"out-home-{suffix}",
                "eventId": "match-1",
                "code": "#Pteam-home",
                "paramParticipantId1": "team-home",
                "translatedName": "North City",
            },
            {
                "_type": "OUTCOME",
                "id": f"out-draw-{suffix}",
                "eventId": "match-1",
                "code": "#D",
                "translatedName": "Berabere",
            },
            {
                "_type": "OUTCOME",
                "id": f"out-away-{suffix}",
                "eventId": "match-1",
                "code": "#Pteam-away",
                "paramParticipantId1": "team-away",
                "translatedName": "South United",
            },
            {
                "_type": "BETTING_OFFER",
                "id": f"offer-home-{suffix}",
                "outcomeId": f"out-home-{suffix}",
                "odds": "1.87",
                "isAvailable": True,
                "isLive": False,
                "statusId": "1",
            },
            {
                "_type": "BETTING_OFFER",
                "id": f"offer-draw-{suffix}",
                "outcomeId": f"out-draw-{suffix}",
                "odds": "3.80",
                "isAvailable": True,
                "isLive": False,
                "statusId": "1",
            },
            {
                "_type": "BETTING_OFFER",
                "id": f"offer-away-{suffix}",
                "outcomeId": f"out-away-{suffix}",
                "odds": "4.15",
                "isAvailable": True,
                "isLive": False,
                "statusId": "1",
            },
        )

    records = (
        (match, standard_market, early_settlement_market)
        + outcome_records("market-standard", "std")
        + outcome_records("market-early-settlement", "es")
    )

    events = wamp_parser.parse_live_records(
        records, source=Source.SOURCE_A, observed_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
    )

    assert [event.source_event_id for event in events] == ["match-1"]
    assert events[0].quotes[Outcome.HOME].odds == Decimal("1.87")
    assert events[0].quotes[Outcome.DRAW].odds == Decimal("3.80")
    assert events[0].quotes[Outcome.AWAY].odds == Decimal("4.15")


def test_parse_v2_records_excludes_the_early_settlement_variant_of_full_time_result() -> None:
    """The design spec calls for this exclusion in both parsing pipelines: today it is
    a no-op for source_c/source_b's v2 snapshots (they never carry bettingTypeId), but it
    guards parse_v2_records the same way parse_live_records is guarded, in case any
    WAMP-feed source is ever routed through this path with a duplicate market."""
    records = (
        {
            "entityType": "Tournament",
            "id": "v2-tournament",
            "sport": "football",
            "name": "Sample League",
        },
        {
            "entityType": "Match",
            "id": "v2-match",
            "tournamentID": "v2-tournament",
            "sport": "football",
            "home": "North City",
            "away": "South United",
            "kickoff": "2026-08-25T19:00:00Z",
            "status": "prematch",
        },
        {
            "entityType": "Market",
            "id": "v2-market-standard",
            "matchID": "v2-match",
            "name": "Full Time Result",
            "bettingTypeId": "69",
            "status": "open",
        },
        {
            "entityType": "Market",
            "id": "v2-market-early-settlement",
            "matchID": "v2-match",
            "name": "Full Time Result",
            "bettingTypeId": "1510",
            "status": "open",
        },
        {
            "entityType": "BettingOffer",
            "id": "v2-offer-standard",
            "marketID": "v2-market-standard",
            "status": "open",
        },
        {
            "entityType": "BettingOffer",
            "id": "v2-offer-early",
            "marketID": "v2-market-early-settlement",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-home-standard",
            "bettingOfferID": "v2-offer-standard",
            "label": "1",
            "odds": "1.87",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-draw-standard",
            "bettingOfferID": "v2-offer-standard",
            "label": "X",
            "odds": "3.80",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-away-standard",
            "bettingOfferID": "v2-offer-standard",
            "label": "2",
            "odds": "4.15",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-home-early",
            "bettingOfferID": "v2-offer-early",
            "label": "1",
            "odds": "1.01",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-draw-early",
            "bettingOfferID": "v2-offer-early",
            "label": "X",
            "odds": "9.00",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "v2-away-early",
            "bettingOfferID": "v2-offer-early",
            "label": "2",
            "odds": "15.00",
            "status": "open",
        },
    )

    events = parse_v2_records(records, source=Source.SOURCE_E, observed_at=OBSERVED_AT)

    assert [event.source_event_id for event in events] == ["v2-match"]
    assert events[0].quotes[Outcome.HOME].odds == Decimal("1.87")
    assert events[0].quotes[Outcome.DRAW].odds == Decimal("3.80")
    assert events[0].quotes[Outcome.AWAY].odds == Decimal("4.15")


def test_parse_live_records_recognizes_turkish_full_time_result_market_name() -> None:
    from surebet.sources.wamp_feed.parser import _is_live_ftr_market

    assert _is_live_ftr_market("Kim Kazan\u0131r, Normal S\u00fcre")
    assert _is_live_ftr_market("Ma\u00e7 Sonucu, Normal S\u00fcre")


@pytest.mark.parametrize(
    ("fixture_name", "source", "event_id", "competition", "home", "away", "kickoff", "odds"),
    [
        (
            "source_c_football_1x2.json",
            Source.SOURCE_C,
            "312173910072913920",
            "UEFA Avrupa Konferans Ligi Elemeleri",
            "Iberia 1999",
            "Jagiellonia Białystok",
            datetime(2026, 8, 27, 16, 0, tzinfo=UTC),
            {
                Outcome.HOME: Decimal("3.30"),
                Outcome.DRAW: Decimal("3.50"),
                Outcome.AWAY: Decimal("2.02"),
            },
        ),
        (
            "source_b_football_1x2.json",
            Source.SOURCE_B,
            "312184457444093952",
            "UEFA Avrupa Konferans Ligi Elemeleri",
            "Jablonec",
            "Rangers",
            datetime(2026, 8, 27, 16, 0, tzinfo=UTC),
            {
                Outcome.HOME: Decimal("3.00"),
                Outcome.DRAW: Decimal("3.40"),
                Outcome.AWAY: Decimal("2.23"),
            },
        ),
    ],
)
def test_parse_live_records_normalizes_complete_future_football_full_time_result(
    fixture_name: str,
    source: Source,
    event_id: str,
    competition: str,
    home: str,
    away: str,
    kickoff: datetime,
    odds: dict[Outcome, Decimal],
) -> None:
    events = parse_live_records(
        load_live_records(fixture_name),
        source=source,
        observed_at=OBSERVED_AT,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source is source
    assert event.source_event_id == event_id
    assert event.competition == competition
    assert event.home == home
    assert event.away == away
    assert event.kickoff == kickoff
    assert event.market_key == FULL_TIME_RESULT_MARKET
    assert set(event.quotes) == set(Outcome)
    assert len(event.quotes) == 3
    assert {outcome: quote.odds for outcome, quote in event.quotes.items()} == odds
    assert all(
        quote.source is source and quote.event_id == event_id for quote in event.quotes.values()
    )


def test_parse_live_records_rounds_down_provider_odds_beyond_four_decimals() -> None:
    """A valid match must not disappear when the WAMP feed exposes reciprocal-style odds."""
    records = [dict(record) for record in load_live_records("source_c_football_1x2.json")]
    home_offer = next(
        record
        for record in records
        if record.get("_type") == "BETTING_OFFER"
        and record.get("outcomeId") == "sanitized-home-outcome"
    )
    home_offer["odds"] = "1.4545455"

    events = parse_live_records(records, source=Source.SOURCE_C, observed_at=OBSERVED_AT)

    assert [event.source_event_id for event in events] == ["312173910072913920"]
    assert events[0].quotes[Outcome.HOME].odds == Decimal("1.4545")


def test_live_taxonomy_fixtures_each_contain_one_collectable_graph() -> None:
    source_c_events = parse_live_records(
        load_live_records("source_c_football_1x2.json"),
        source=Source.SOURCE_C,
        observed_at=OBSERVED_AT,
    )
    source_b_events = parse_live_records(
        load_live_records("source_b_football_1x2.json"),
        source=Source.SOURCE_B,
        observed_at=OBSERVED_AT,
    )

    assert [event.source_event_id for event in source_c_events] == ["312173910072913920"]
    assert [event.source_event_id for event in source_b_events] == ["312184457444093952"]


def test_live_records_keep_structured_wamp_feed_ids_in_source_namespaces() -> None:
    source_c_event = parse_live_records(
        load_live_records("source_c_football_1x2.json"),
        source=Source.SOURCE_C,
        observed_at=OBSERVED_AT,
    )[0]
    source_b_event = parse_live_records(
        load_live_records("source_b_football_1x2.json"),
        source=Source.SOURCE_B,
        observed_at=OBSERVED_AT,
    )[0]

    source_c_evidence = source_c_event.source_competition
    source_b_evidence = source_b_event.source_competition
    assert source_c_evidence is not None
    assert source_b_evidence is not None
    assert source_c_evidence.provider == "source_c"
    assert source_b_evidence.provider == "source_b"
    assert source_c_evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
    assert source_b_evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
    assert source_c_evidence.competition_id == "sanitized-source_c-tournament"
    assert source_b_evidence.competition_id == "sanitized-source_b-tournament"
    assert source_c_evidence.competition_name == "UEFA Avrupa Konferans Ligi Elemeleri"
    assert source_b_evidence.competition_name == "UEFA Avrupa Konferans Ligi Elemeleri"
    assert source_c_evidence.country_id == "sanitized-source_c-international"
    assert source_b_evidence.country_id == "sanitized-source_b-international"
    assert source_c_evidence.country_name == "Uluslararası"
    assert source_b_evidence.country_name == "Uluslararası"


def test_live_records_without_complete_parent_identity_stay_text_derived() -> None:
    records = [dict(record) for record in load_live_records("source_c_football_1x2.json")]
    match = next(record for record in records if record["_type"] == "MATCH")
    match.pop("parentId")
    match.pop("parentName")
    match.pop("categoryId")

    event = parse_live_records(records, source=Source.SOURCE_C, observed_at=OBSERVED_AT)[0]

    evidence = event.source_competition
    assert event.competition == "Uluslararası"
    assert evidence is not None
    assert evidence.identity_kind is SourceIdentityKind.DERIVED_TEXT
    assert evidence.competition_id.startswith("text:")
    assert evidence.competition_name == "Uluslararası"
    assert evidence.country_id is None
    assert evidence.country_name is None


def test_parse_envelope_wraps_malformed_required_result_fields_in_safe_protocol_error() -> None:
    with pytest.raises(WampFeedProtocolError, match="result envelope"):
        parse_envelope({"format": "json", "messageType": "RESULT", "records": []})


def test_parse_records_raises_safe_protocol_error_for_malformed_required_record_field() -> None:
    envelope = parse_envelope(
        {
            "format": "json",
            "messageType": "RESULT",
            "version": "1",
            "records": [{"_type": "match", "id": "match-without-tournament"}],
        }
    )

    with pytest.raises(WampFeedProtocolError, match="match.tournamentId"):
        parse_records((envelope,), source=Source.SOURCE_C, observed_at=OBSERVED_AT)


def test_parse_records_rejects_duplicate_child_record_ids() -> None:
    envelope = parse_envelope(
        {
            "format": "json",
            "messageType": "RESULT",
            "version": "1",
            "records": [
                {
                    "_type": "tournament",
                    "id": "tournament",
                    "sport": "football",
                    "name": "Sample League",
                },
                {
                    "_type": "match",
                    "id": "match",
                    "tournamentId": "tournament",
                    "sport": "football",
                    "home": "Home",
                    "away": "Away",
                    "kickoff": "2026-08-25T19:00:00Z",
                    "status": "prematch",
                },
                {
                    "_type": "market",
                    "id": "market",
                    "matchId": "match",
                    "name": "Full Time Result",
                    "status": "open",
                },
                {
                    "_type": "bettingOffer",
                    "id": "offer",
                    "marketId": "market",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "home-outcome",
                    "bettingOfferId": "offer",
                    "label": "1",
                    "odds": "2.00",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "draw-outcome",
                    "bettingOfferId": "offer",
                    "label": "X",
                    "odds": "3.00",
                    "status": "open",
                },
                {
                    "_type": "outcome",
                    "id": "home-outcome",
                    "bettingOfferId": "offer",
                    "label": "2",
                    "odds": "4.00",
                    "status": "open",
                },
            ],
        }
    )

    with pytest.raises(WampFeedProtocolError, match="duplicate outcome ID"):
        parse_records((envelope,), source=Source.SOURCE_C, observed_at=OBSERVED_AT)


def test_parse_envelope_exception_traceback_omits_raw_payload_contents() -> None:
    """Chaining schema validation details would expose an incompatible public envelope."""
    payload = {"private_value": "must-" + "not-appear"}
    with pytest.raises(WampFeedProtocolError) as caught:
        parse_envelope(payload)

    assert "must-" + "not-appear" not in "".join(traceback.format_exception(caught.value))


def test_parse_records_normalizes_an_offset_observation_timestamp_to_utc() -> None:
    """Exporting an offset observation timestamp would make equivalent snapshots serialize differently."""
    observed_at = datetime(
        2026, 8, 24, 10, 0, tzinfo=datetime.fromisoformat("2026-08-24T00:00:00+03:00").tzinfo
    )
    events = parse_live_records(
        load_live_records("source_c_football_1x2.json"),
        source=Source.SOURCE_C,
        observed_at=observed_at,
    )

    observed = events[0].quotes[Outcome.HOME].observed_at
    assert observed == OBSERVED_AT
    assert observed.tzinfo is UTC
    assert observed.utcoffset() == UTC.utcoffset(observed)


def live_ftr_graph(*extra_offers: dict[str, object]) -> tuple[object, ...]:
    """A complete current-aggregator 1-X-2 graph, plus any extra betting offers."""
    return (
        {
            "_type": "MATCH",
            "id": "match-1",
            "sportId": "1",
            "sportName": "Futbol",
            "categoryName": "League",
            "homeParticipantId": "team-home",
            "awayParticipantId": "team-away",
            "homeParticipantName": "North City",
            "awayParticipantName": "South United",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        {
            "_type": "MARKET",
            "id": "market-1",
            "eventId": "match-1",
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-home",
            "marketId": "market-1",
            "outcomeId": "out-home",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-draw",
            "marketId": "market-1",
            "outcomeId": "out-draw",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-away",
            "marketId": "market-1",
            "outcomeId": "out-away",
        },
        {
            "_type": "OUTCOME",
            "id": "out-home",
            "eventId": "match-1",
            "code": "#Pteam-home",
            "paramParticipantId1": "team-home",
            "translatedName": "North City",
        },
        {
            "_type": "OUTCOME",
            "id": "out-draw",
            "eventId": "match-1",
            "code": "#D",
            "translatedName": "Draw",
        },
        {
            "_type": "OUTCOME",
            "id": "out-away",
            "eventId": "match-1",
            "code": "#Pteam-away",
            "paramParticipantId1": "team-away",
            "translatedName": "South United",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-home",
            "outcomeId": "out-home",
            "odds": "2.10",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-draw",
            "outcomeId": "out-draw",
            "odds": "3.30",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "offer-away",
            "outcomeId": "out-away",
            "odds": "3.60",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        *extra_offers,
    )


def test_parse_live_records_drops_a_match_whose_outcome_carries_disagreeing_odds() -> None:
    """Two prematch topics are merged into one record set, so one outcome can end up holding two
    offers. Publishing whichever happened to be reduced first would surface an odds value the
    operator never saw as authoritative, so an unresolvable conflict must drop the match."""
    records = live_ftr_graph(
        {
            "_type": "BETTING_OFFER",
            "id": "offer-home-alt",
            "outcomeId": "out-home",
            "odds": "9.99",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
    )

    events = parse_live_records(records, source=Source.SOURCE_C, observed_at=OBSERVED_AT)

    assert events == ()


def test_parse_live_records_keeps_a_match_whose_duplicate_offers_agree_on_odds() -> None:
    """Duplicate offers are only ambiguous when they disagree; identical prices are not a conflict
    and must not cost the operator a match."""
    records = live_ftr_graph(
        {
            "_type": "BETTING_OFFER",
            "id": "offer-home-alt",
            "outcomeId": "out-home",
            "odds": "2.10",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
    )

    events = parse_live_records(records, source=Source.SOURCE_C, observed_at=OBSERVED_AT)

    assert [event.source_event_id for event in events] == ["match-1"]
    assert events[0].quotes[Outcome.HOME].odds == Decimal("2.10")


def test_parse_live_records_extracts_ou25_market() -> None:
    records = (
        {
            "_type": "MATCH",
            "id": "match-2way",
            "sportId": "1",
            "sportName": "Futbol",
            "parentId": "tournament-1",
            "parentName": "Super Lig",
            "categoryId": "category-tr",
            "categoryName": "Turkiye",
            "homeParticipantId": "team-h",
            "awayParticipantId": "team-a",
            "homeParticipantName": "Galatasaray",
            "awayParticipantName": "Fenerbahce",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        # 1X2 Market
        {
            "_type": "MARKET",
            "id": "market-ftr",
            "eventId": "match-2way",
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-1",
            "marketId": "market-ftr",
            "outcomeId": "out-1",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-x",
            "marketId": "market-ftr",
            "outcomeId": "out-x",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-2",
            "marketId": "market-ftr",
            "outcomeId": "out-2",
        },
        {
            "_type": "OUTCOME",
            "id": "out-1",
            "eventId": "match-2way",
            "code": "#Pteam-h",
            "paramParticipantId1": "team-h",
        },
        {"_type": "OUTCOME", "id": "out-x", "eventId": "match-2way", "code": "#D"},
        {
            "_type": "OUTCOME",
            "id": "out-2",
            "eventId": "match-2way",
            "code": "#Pteam-a",
            "paramParticipantId1": "team-a",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-1",
            "outcomeId": "out-1",
            "odds": "2.20",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-x",
            "outcomeId": "out-x",
            "odds": "3.40",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-2",
            "outcomeId": "out-2",
            "odds": "3.10",
            "isAvailable": True,
            "isLive": False,
        },
        # OU 2.5 Market
        {
            "_type": "MARKET",
            "id": "market-ou",
            "eventId": "match-2way",
            "name": "Toplam Gol 2.5",
            "bettingTypeId": "47",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-o",
            "marketId": "market-ou",
            "outcomeId": "out-o",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-u",
            "marketId": "market-ou",
            "outcomeId": "out-u",
        },
        {
            "_type": "OUTCOME",
            "id": "out-o",
            "eventId": "match-2way",
            "code": "#O",
            "headerName": "Üst",
        },
        {
            "_type": "OUTCOME",
            "id": "out-u",
            "eventId": "match-2way",
            "code": "#U",
            "headerName": "Alt",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-o",
            "outcomeId": "out-o",
            "odds": "1.92",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-u",
            "outcomeId": "out-u",
            "odds": "1.95",
            "isAvailable": True,
            "isLive": False,
        },
    )

    events = parse_live_records(records, source=Source.SOURCE_B, observed_at=OBSERVED_AT)

    assert len(events) == 1
    ev = events[0]
    assert ev.ou25_quotes is not None
    assert ev.ou25_quotes[TwoWayOutcome.OVER].odds == Decimal("1.92")
    assert ev.ou25_quotes[TwoWayOutcome.OVER].market_type == MarketType.TOTAL_GOALS_25
    assert ev.ou25_quotes[TwoWayOutcome.UNDER].odds == Decimal("1.95")


def test_parse_live_records_resolves_parent_tournament_for_subgroups() -> None:
    records = (
        # Top tournament relationship
        {
            "_type": "TOURNAMENT",
            "id": "302078972833075200",
            "parentId": "910000000000000109",
            "name": "UEFA Uluslar Ligi A, Grup 1 2026",
            "parentName": "UEFA Uluslar Ligi 2026/2027",
        },
        # Match inside the sub-tournament
        {
            "_type": "MATCH",
            "id": "match-nl-1",
            "sportId": "1",
            "sportName": "Futbol",
            "parentId": "302078972833075200",
            "parentName": "UEFA Uluslar Ligi A, Grup 1 2026",
            "categoryId": "67001",
            "categoryName": "Uluslararası",
            "homeParticipantId": "team-tur",
            "awayParticipantId": "team-fra",
            "homeParticipantName": "Türkiye",
            "awayParticipantName": "Fransa",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        {
            "_type": "MARKET",
            "id": "market-nl-ftr",
            "eventId": "match-nl-1",
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-nl-1",
            "marketId": "market-nl-ftr",
            "outcomeId": "out-nl-1",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-nl-x",
            "marketId": "market-nl-ftr",
            "outcomeId": "out-nl-x",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": "rel-nl-2",
            "marketId": "market-nl-ftr",
            "outcomeId": "out-nl-2",
        },
        {
            "_type": "OUTCOME",
            "id": "out-nl-1",
            "eventId": "match-nl-1",
            "code": "#Pteam-tur",
            "paramParticipantId1": "team-tur",
        },
        {"_type": "OUTCOME", "id": "out-nl-x", "eventId": "match-nl-1", "code": "#D"},
        {
            "_type": "OUTCOME",
            "id": "out-nl-2",
            "eventId": "match-nl-1",
            "code": "#Pteam-fra",
            "paramParticipantId1": "team-fra",
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-nl-1",
            "outcomeId": "out-nl-1",
            "odds": "2.80",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-nl-x",
            "outcomeId": "out-nl-x",
            "odds": "3.30",
            "isAvailable": True,
            "isLive": False,
        },
        {
            "_type": "BETTING_OFFER",
            "id": "off-nl-2",
            "outcomeId": "out-nl-2",
            "odds": "2.35",
            "isAvailable": True,
            "isLive": False,
        },
    )

    events = parse_live_records(records, source=Source.SOURCE_B, observed_at=OBSERVED_AT)

    assert len(events) == 1
    ev = events[0]
    assert ev.competition == "UEFA Uluslar Ligi 2026/2027"
    assert ev.source_competition is not None
    assert ev.source_competition.competition_id == "910000000000000109"
    assert ev.source_competition.competition_name == "UEFA Uluslar Ligi 2026/2027"
    assert ev.source_competition.country_name == "Uluslararası"
    assert ev.quotes[Outcome.HOME].odds == Decimal("2.80")

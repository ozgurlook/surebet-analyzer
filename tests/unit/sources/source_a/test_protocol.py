import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from surebet.domain.enums import MarketType, Outcome, Source, TwoWayOutcome
from surebet.sources.models import FULL_TIME_RESULT_MARKET
from surebet.sources.source_a.protocol import (
    build_games_query,
    build_markets_query,
    parse_collection,
)

FIXTURE = Path("tests/fixtures/sources/source_a/public_1x2_response.json")
OBSERVED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)


def test_games_query_requests_only_public_prematch_soccer_identity_fields() -> None:
    assert build_games_query() == {
        "source": "betting",
        "what": {
            "sport": ["id", "name", "alias"],
            "region": ["id", "name", "alias"],
            "competition": ["id", "name"],
            "game": [
                "id",
                "team1_name",
                "team2_name",
                "start_ts",
                "is_blocked",
                "sport_alias",
            ],
        },
        "where": {
            "sport": {"alias": "Soccer"},
            "game": {
                "@or": [
                    {"visible_in_prematch": 1},
                    {"type": {"@in": [0, 2]}},
                ]
            },
        },
    }


def test_markets_query_requests_only_full_time_1x2_prices() -> None:
    assert build_markets_query() == {
        "source": "betting",
        "what": {
            "sport": ["id"],
            "region": ["id"],
            "competition": ["id"],
            "game": ["id"],
            "market": ["id", "name", "type", "display_key"],
            "event": ["id", "type_1", "price"],
        },
        "where": {
            "sport": {"alias": "Soccer"},
            "game": {
                "@or": [
                    {"visible_in_prematch": 1},
                    {"type": {"@in": [0, 2]}},
                ]
            },
            "market": {
                "type": "P1XP2",
                "display_key": "WINNER",
                "display_sub_key": "MATCH",
            },
        },
    }


def test_parse_collection_maps_complete_p1xp2_to_exact_canonical_quotes() -> None:
    fixture = load_fixture()

    events = parse_collection(
        fixture["games_data"], fixture["markets_data"], observed_at=OBSERVED_AT
    )

    assert len(events) == 1
    event = events[0]
    assert event.source is Source.SOURCE_A
    assert event.source_event_id == "30549576"
    assert event.competition == "UEFA Avrupa Ligi"
    assert event.home == "Bodo/Glimt"
    assert event.away == "NEC Nijmegen"
    assert event.kickoff == datetime(2026, 8, 25, 19, 0, tzinfo=UTC)
    assert event.market_key == FULL_TIME_RESULT_MARKET
    assert {outcome: quote.odds for outcome, quote in event.quotes.items()} == {
        Outcome.HOME: Decimal("1.4700"),
        Outcome.DRAW: Decimal("4.3300"),
        Outcome.AWAY: Decimal("4.6000"),
    }


def test_parse_collection_accepts_current_rpc_data_envelope() -> None:
    fixture = load_fixture()

    events = parse_collection(
        {"data": fixture["games_data"]},
        {"data": fixture["markets_data"]},
        observed_at=OBSERVED_AT,
    )

    assert [event.source_event_id for event in events] == ["30549576"]


def test_parse_collection_rejects_duplicate_incomplete_wrong_or_blocked_markets() -> None:
    fixture = load_fixture()

    events = parse_collection(
        fixture["games_data"], fixture["markets_data"], observed_at=OBSERVED_AT
    )

    assert [event.source_event_id for event in events] == ["30549576"]


def test_parse_collection_rejects_events_whose_kickoff_is_not_future() -> None:
    fixture = load_fixture()
    games = fixture["games_data"]
    game = games["sport"]["1"]["region"]["20"]["competition"]["18286520"]["game"]["30549576"]  # type: ignore[index]
    game["start_ts"] = int(OBSERVED_AT.timestamp())  # type: ignore[index]

    events = parse_collection(games, fixture["markets_data"], observed_at=OBSERVED_AT)

    assert events == ()


def test_parse_collection_is_deterministic_by_kickoff_then_event_id() -> None:
    fixture = load_fixture()
    games = fixture["games_data"]
    markets = fixture["markets_data"]
    competition_games = games["sport"]["1"]["region"]["20"]["competition"]["18286520"]["game"]  # type: ignore[index]
    competition_markets = markets["sport"]["1"]["region"]["20"]["competition"]["18286520"]["game"]  # type: ignore[index]
    competition_games["9"] = {  # type: ignore[index]
        "id": 9,
        "team1_name": "Earlier Home",
        "team2_name": "Earlier Away",
        "start_ts": 1787600000,
        "is_blocked": 0,
        "sport_alias": "Soccer",
    }
    competition_markets["9"] = {  # type: ignore[index]
        "id": 9,
        "market": {
            "9": {
                "id": 9,
                "name": "Maç Sonucu",
                "type": "P1XP2",
                "display_key": "WINNER",
                "event": {
                    "1": {"id": 1, "type_1": "W1", "price": Decimal("2.10")},
                    "2": {"id": 2, "type_1": "X", "price": Decimal("3.20")},
                    "3": {"id": 3, "type_1": "W2", "price": Decimal("3.40")},
                },
            }
        },
    }

    events = parse_collection(games, markets, observed_at=OBSERVED_AT)

    assert [event.source_event_id for event in events] == ["9", "30549576"]


def test_parse_collection_maps_ou25_market() -> None:
    fixture = load_fixture()
    games = fixture["games_data"]
    markets = fixture["markets_data"]

    ou25_data = {
        "game": {
            "30549576": {
                "id": "30549576",
                "market": {
                    "m_ou": {
                        "id": "m_ou",
                        "type": "OverUnder",
                        "base": 2.5,
                        "event": {
                            "e_o": {"id": "e_o", "type_1": "Over", "price": Decimal("1.88")},
                            "e_u": {"id": "e_u", "type_1": "Under", "price": Decimal("1.98")},
                        },
                    }
                },
            }
        }
    }

    events = parse_collection(
        games,
        markets,
        ou25_data=ou25_data,
        observed_at=OBSERVED_AT,
    )

    assert len(events) == 1
    event = events[0]
    assert event.ou25_quotes is not None
    assert event.ou25_quotes[TwoWayOutcome.OVER].odds == Decimal("1.88")
    assert event.ou25_quotes[TwoWayOutcome.OVER].market_type == MarketType.TOTAL_GOALS_25
    assert event.ou25_quotes[TwoWayOutcome.UNDER].odds == Decimal("1.98")

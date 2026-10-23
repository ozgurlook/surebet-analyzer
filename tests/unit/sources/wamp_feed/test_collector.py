import asyncio
import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest

from surebet.domain.enums import Outcome, Source
from surebet.sources.wamp_feed.collector import _initial_dump_topics, collect_snapshot
from surebet.sources.wamp_feed.contracts import (
    REGISTRATION_SUFFIXES,
    SUBSCRIBE_TOPICS,
    tournament_dump_topic,
    tournament_registration_suffix,
)
from surebet.sources.wamp_feed.models import WampFeedSourceConfig
from tests.conftest import example_endpoint

STARTED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=2)
MAIN_PREMATCH_TOPIC = "/sports/1001/tr/next-matches-aggregator-main/1/0-180/180/4"
POPULAR_PREMATCH_TOPIC = "/sports/1001/tr/popular-matches-aggregator-groups/1/180/4"
TOURNAMENT_ID_NL = "910000000000000109"
TOURNAMENT_TOPIC_SOURCE_C = tournament_dump_topic(1001, TOURNAMENT_ID_NL)
# Both operator ids the collectors use, so a mistyped topic in a test is rejected
# instead of quietly behaving like a topic that returns nothing.
KNOWN_DUMP_TOPICS = frozenset(
    topic for operator_id in (1001, 1002) for topic in _initial_dump_topics(operator_id)
) | {TOURNAMENT_TOPIC_SOURCE_C}


def clock_values(*values: datetime) -> Any:
    iterator: Iterator[datetime] = iter(values)
    return lambda: next(iterator)


def initial_records() -> list[dict[str, object]]:
    return [
        {"entityType": "Tournament", "id": "league", "sport": "football", "name": "League"},
        {
            "entityType": "Match",
            "id": "fixture",
            "tournamentID": "league",
            "sport": "football",
            "home": "Home",
            "away": "Away",
            "kickoff": "2026-08-25T19:00:00Z",
            "status": "prematch",
        },
        {
            "entityType": "Market",
            "id": "market",
            "matchID": "fixture",
            "name": "Full Time Result",
            "status": "open",
        },
        {"entityType": "BettingOffer", "id": "offer", "marketID": "market", "status": "open"},
        {
            "entityType": "Outcome",
            "id": "home",
            "bettingOfferID": "offer",
            "label": "1",
            "odds": "2.10",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "draw",
            "bettingOfferID": "offer",
            "label": "X",
            "odds": "3.30",
            "status": "open",
        },
        {
            "entityType": "Outcome",
            "id": "away",
            "bettingOfferID": "offer",
            "label": "2",
            "odds": "3.60",
            "status": "open",
        },
    ]


def live_match_records(match_id: str) -> list[dict[str, object]]:
    """One complete 1-X-2 current-aggregator record graph for a single match id."""
    home, away = f"home-{match_id}", f"away-{match_id}"
    market = f"market-{match_id}"
    return [
        {
            "_type": "MATCH",
            "id": match_id,
            "sportId": "1",
            "sportName": "Futbol",
            "categoryName": "League",
            "homeParticipantId": home,
            "awayParticipantId": away,
            "homeParticipantName": "Home",
            "awayParticipantName": "Away",
            "startTime": 1787684400000,
            "statusName": "Beklemede",
        },
        {
            "_type": "MARKET",
            "id": market,
            "eventId": match_id,
            "name": "Full Time Result",
            "isAvailable": True,
            "isClosed": False,
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": f"rel-home-{match_id}",
            "marketId": market,
            "outcomeId": f"out-home-{match_id}",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": f"rel-draw-{match_id}",
            "marketId": market,
            "outcomeId": f"out-draw-{match_id}",
        },
        {
            "_type": "MARKET_OUTCOME_RELATION",
            "id": f"rel-away-{match_id}",
            "marketId": market,
            "outcomeId": f"out-away-{match_id}",
        },
        {
            "_type": "OUTCOME",
            "id": f"out-home-{match_id}",
            "eventId": match_id,
            "code": f"#P {home}",
            "paramParticipantId1": home,
            "translatedName": "Home",
        },
        {
            "_type": "OUTCOME",
            "id": f"out-draw-{match_id}",
            "eventId": match_id,
            "code": "#D",
            "translatedName": "Draw",
        },
        {
            "_type": "OUTCOME",
            "id": f"out-away-{match_id}",
            "eventId": match_id,
            "code": f"#P {away}",
            "paramParticipantId1": away,
            "translatedName": "Away",
        },
        {
            "_type": "BETTING_OFFER",
            "id": f"offer-home-{match_id}",
            "outcomeId": f"out-home-{match_id}",
            "odds": "2.10",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": f"offer-draw-{match_id}",
            "outcomeId": f"out-draw-{match_id}",
            "odds": "3.30",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
        {
            "_type": "BETTING_OFFER",
            "id": f"offer-away-{match_id}",
            "outcomeId": f"out-away-{match_id}",
            "odds": "3.60",
            "isAvailable": True,
            "isLive": False,
            "statusId": "1",
        },
    ]


def current_live_records() -> list[dict[str, object]]:
    return live_match_records("match-1")


def envelope(records: Iterable[dict[str, object]]) -> dict[str, object]:
    return {"format": "json", "messageType": "RESULT", "records": list(records), "version": "2"}


class ScriptedConnection:
    """A fully local WAMP transcript containing only sanitized v2 records."""

    def __init__(
        self,
        *,
        failed_topics: Iterable[str] = (),
        records: Iterable[dict[str, object]] | None = None,
        records_by_topic: Mapping[str, Iterable[dict[str, object]]] | None = None,
        delta_delay_seconds: float = 0,
    ) -> None:
        self._failed_topics = set(failed_topics)
        self._records = list(initial_records() if records is None else records)
        self._records_by_topic = (
            None
            if records_by_topic is None
            else {topic: list(entries) for topic, entries in records_by_topic.items()}
        )
        named = self._failed_topics | set(self._records_by_topic or ())
        if unknown := sorted(named - KNOWN_DUMP_TOPICS):
            raise ValueError(f"topics the collector never requests: {unknown}")
        self._delta_delay_seconds = delta_delay_seconds
        self.replies: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[list[object]] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def send(self, message: str) -> None:
        frame = json.loads(message)
        self.sent.append(frame)
        if frame[0] == 6:
            return
        if frame[0] == 1:
            await self.replies.put(json.dumps([2, 1, {}]))
        elif frame[0] == 64:
            await self.replies.put(json.dumps([65, frame[1], 100 + len(self.sent)]))
        elif frame[0] == 32:
            await self.replies.put(json.dumps([33, frame[1], 200]))
        elif frame[0] == 48:
            procedure = frame[3]
            kwargs = frame[5]
            if procedure == "/sports#initialDump" and kwargs["topic"] in self._failed_topics:
                await self.replies.put(
                    json.dumps([8, 48, frame[1], {}, "wamp.error.runtime_error"])
                )
                return
            result: object = envelope(())
            if procedure == "/sports#initialDump" and kwargs["topic"] not in self._failed_topics:
                result = envelope(
                    self._records
                    if self._records_by_topic is None
                    else self._records_by_topic.get(kwargs["topic"], ())
                )
            await self.replies.put(json.dumps([50, frame[1], {}, [result]]))
            if procedure == "/sports#marketGroupsOverview":
                delta = envelope(
                    [
                        {
                            "entityType": "Outcome",
                            "id": "draw",
                            "changeType": "update",
                            "changedProperties": {"odds": "3.40"},
                        }
                    ]
                )
                if self._delta_delay_seconds:
                    asyncio.create_task(self._enqueue_delta(delta))
                else:
                    await self.replies.put(json.dumps([68, 301, 101, {}, [], delta]))

    async def _enqueue_delta(self, delta: dict[str, object]) -> None:
        await asyncio.sleep(self._delta_delay_seconds)
        await self.replies.put(json.dumps([68, 301, 101, {}, [], delta]))

    async def recv(self) -> str:
        return await self.replies.get()

    async def close(self) -> None:
        self.closed = True


class HangingConnection:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def send(self, _message: str) -> None:
        return None

    async def recv(self) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class StalledCloseConnection(ScriptedConnection):
    async def close(self) -> None:
        await asyncio.Event().wait()


class StalledExitConnection(ScriptedConnection):
    def __init__(self) -> None:
        super().__init__()
        self.exit_started = asyncio.Event()

    async def __aexit__(self, *_: object) -> None:
        self.exit_started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_collect_snapshot_bootstraps_v2_state_and_acknowledges_deltas() -> None:
    """Wrong bootstrap kwargs, skipped deltas, or a raw YIELD result would break the live v2 contract."""
    bad_topic = "/sports/1001/tr/custom-sports"
    connection = ScriptedConnection(failed_topics=(bad_topic,))
    config = WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C))

    batch = await collect_snapshot(
        config=config,
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert batch.source is Source.SOURCE_C
    assert batch.started_at == STARTED_AT
    assert batch.finished_at == FINISHED_AT
    assert len(batch.events) == 1
    assert batch.events[0].quotes[Outcome.DRAW].odds == Decimal("3.40")
    assert connection.closed
    assert [frame[3] for frame in connection.sent if frame[0] == 64] == [
        f"/sports/1001/tr/{suffix}" for suffix in REGISTRATION_SUFFIXES
    ]
    assert [frame[3] for frame in connection.sent if frame[0] == 32] == list(SUBSCRIBE_TOPICS)

    calls = [frame for frame in connection.sent if frame[0] == 48]
    assert [frame[3] for frame in calls] == [
        "/sports#getSessionInfo",
        "/sports#configureFonts",
        *(["/sports#initialDump"] * 9),
        "/sports#marketGroupsOverview",
    ]
    context = calls[0][5]["ctx"]
    assert calls[0][5] == {"ctx": context, "lang": "tr"}
    assert calls[1][5] == {"ctx": context}
    assert [frame[5]["topic"] for frame in calls[2:11]] == [
        "/sports/1001/tr/custom-sports",
        "/sports/1001/tr/next-sports-by-popularity-with-live/180",
        "/sports/1001/tr/disciplines/BOTH/BOTH",
        "/sports/1001/tr/custom-events",
        "/sports/1001/tr/next-locations-by-popularity-with-live/1/180",
        "/sports/1001/tr/time-ranges-discipline/1/180",
        "/sports/1001/tr/live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
        "/sports/1001/tr/next-matches-aggregator-main/1/0-180/180/4",
        "/sports/1001/tr/popular-matches-aggregator-groups/1/180/4",
    ]
    assert all(frame[5]["ctx"] == context for frame in calls)
    assert calls[-1][5] == {"ctx": context, "lang": "tr", "liveStatus": "LIVE", "sportId": "1"}
    assert context["v"] == "2"
    assert context["t"] == 1787554800000
    assert [frame for frame in connection.sent if frame[0] == 70] == [
        [70, 301, {}, [{"status": "ok"}]]
    ]


@pytest.mark.asyncio
async def test_collect_snapshot_normalizes_current_live_aggregator_records() -> None:
    connection = ScriptedConnection(records=current_live_records())

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert [event.source_event_id for event in batch.events] == ["match-1"]
    assert batch.events[0].quotes[Outcome.HOME].odds == Decimal("2.10")


@pytest.mark.asyncio
async def test_collect_snapshot_drains_delta_arriving_before_collection_deadline() -> None:
    """Returning after a short idle period would lose a valid live delta before the deadline."""
    connection = ScriptedConnection(delta_delay_seconds=0.075)
    loop = asyncio.get_running_loop()
    started = loop.time()

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=0.2,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert batch.events[0].quotes[Outcome.DRAW].odds == Decimal("3.40")
    assert 0.15 <= loop.time() - started < 0.25
    assert [frame for frame in connection.sent if frame[0] == 70] == [
        [70, 301, {}, [{"status": "ok"}]]
    ]


@pytest.mark.asyncio
async def test_collect_snapshot_honors_exact_caller_timeout() -> None:
    """Extending the outer timeout for drain cleanup also extends a stalled HELLO beyond caller policy."""
    connection = HangingConnection()
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(TimeoutError):
        await collect_snapshot(
            config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
            registration_suffixes=REGISTRATION_SUFFIXES,
            connection_factory=lambda _config: connection,
            timeout_seconds=0.02,
            clock=clock_values(STARTED_AT),
        )

    assert loop.time() - started < 0.06
    assert connection.closed


@pytest.mark.asyncio
async def test_collect_snapshot_does_not_wait_for_stalled_close_after_deadline() -> None:
    """Awaiting a transport close after deadline would let a seeded batch hang forever."""
    connection = StalledCloseConnection()

    batch = await asyncio.wait_for(
        collect_snapshot(
            config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
            registration_suffixes=REGISTRATION_SUFFIXES,
            connection_factory=lambda _config: connection,
            timeout_seconds=0.05,
            clock=clock_values(STARTED_AT, FINISHED_AT),
        ),
        timeout=0.2,
    )

    assert len(batch.events) == 1
    assert connection.closed


@pytest.mark.asyncio
async def test_collect_snapshot_does_not_wait_for_stalled_context_manager_exit() -> None:
    """A connection manager exit must share the exact collection deadline."""
    connection = StalledExitConnection()

    batch = await asyncio.wait_for(
        collect_snapshot(
            config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
            registration_suffixes=REGISTRATION_SUFFIXES,
            connection_factory=lambda _config: connection,
            timeout_seconds=0.05,
            clock=clock_values(STARTED_AT, FINISHED_AT),
        ),
        timeout=0.2,
    )

    assert len(batch.events) == 1
    assert connection.exit_started.is_set()


@pytest.mark.asyncio
async def test_collect_snapshot_accepts_protocol_valid_zero_event_state() -> None:
    """A quiet schedule is a successful live URL test when the protocol contract completed."""
    connection = ScriptedConnection(records=())

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert batch.source is Source.SOURCE_C
    assert batch.events == ()
    assert connection.closed


@pytest.mark.asyncio
async def test_collect_snapshot_merges_matches_unique_to_each_prematch_topic() -> None:
    """Each prematch topic is truncated at the provider's own list cap and the two caps hold
    different fixtures, so a match carried by only one topic must still reach the batch while a
    match carried by both is emitted once.

    The shared match repeats identical child-record ids across both topics because that is the
    measured provider behaviour: across 112 matches served by both topics on both brands, the
    full-time-result market and its relation ids were identical every time. See the cross-topic
    identity invariant in docs/source-contracts/wamp_feed.md."""
    connection = ScriptedConnection(
        records_by_topic={
            MAIN_PREMATCH_TOPIC: [
                *live_match_records("shared"),
                *live_match_records("only-next"),
            ],
            POPULAR_PREMATCH_TOPIC: [
                *live_match_records("shared"),
                *live_match_records("only-popular"),
            ],
        }
    )

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert sorted(event.source_event_id for event in batch.events) == [
        "only-next",
        "only-popular",
        "shared",
    ]


@pytest.mark.asyncio
async def test_collect_snapshot_survives_a_failing_popular_topic() -> None:
    """The popular topic is additive, so when only it errors the main list's fixtures must still
    reach the batch. Only the main topic carries records here, so the surviving event proves the
    main dump was kept rather than the failed topic being masked by another topic's copy."""
    connection = ScriptedConnection(
        failed_topics=(POPULAR_PREMATCH_TOPIC,),
        records_by_topic={MAIN_PREMATCH_TOPIC: current_live_records()},
    )

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert [event.source_event_id for event in batch.events] == ["match-1"]


@pytest.mark.asyncio
async def test_collect_snapshot_fetches_and_merges_tracked_tournaments() -> None:
    connection = ScriptedConnection(
        records_by_topic={
            MAIN_PREMATCH_TOPIC: live_match_records("main-match"),
            TOURNAMENT_TOPIC_SOURCE_C: live_match_records("nl-match"),
        }
    )

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        tournament_ids=(TOURNAMENT_ID_NL,),
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    # Check registration frames
    reg_suffix = tournament_registration_suffix(TOURNAMENT_ID_NL)
    expected_proc = f"/sports/1001/tr/{reg_suffix}"
    registered_procs = [frame[3] for frame in connection.sent if frame[0] == 64]
    assert expected_proc in registered_procs

    # Check initialDump calls
    dump_topics = [
        frame[5]["topic"]
        for frame in connection.sent
        if frame[0] == 48 and frame[3] == "/sports#initialDump"
    ]
    assert TOURNAMENT_TOPIC_SOURCE_C in dump_topics

    # Check both events are in batch
    event_ids = sorted(event.source_event_id for event in batch.events)
    assert event_ids == ["main-match", "nl-match"]


@pytest.mark.asyncio
async def test_collect_snapshot_survives_a_failing_tournament_topic() -> None:
    connection = ScriptedConnection(
        failed_topics=(TOURNAMENT_TOPIC_SOURCE_C,),
        records_by_topic={
            MAIN_PREMATCH_TOPIC: live_match_records("main-match"),
        },
    )

    batch = await collect_snapshot(
        config=WampFeedSourceConfig.from_endpoint(example_endpoint(Source.SOURCE_C)),
        registration_suffixes=REGISTRATION_SUFFIXES,
        tournament_ids=(TOURNAMENT_ID_NL,),
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    assert [event.source_event_id for event in batch.events] == ["main-match"]

import asyncio
import json
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import pytest
from websockets.exceptions import WebSocketException

from surebet.domain.enums import Outcome, Source
from surebet.sources.source_e.collector import SourceECollector, SourceEUnavailable
from surebet.sources.wamp_feed.contracts import SUBSCRIBE_TOPICS
from tests.conftest import example_endpoint

FIXTURE = Path("tests/fixtures/sources/wamp_feed/source_e_football_1x2_v2.json")
STARTED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=2)


def clock_values(*values: datetime) -> Any:
    iterator: Iterator[datetime] = iter(values)
    return lambda: next(iterator)


class ScriptedConnection:
    """Anonymous WAMP transcript containing only sanitized fixture envelopes."""

    def __init__(self, *, malformed: bool = False, hang: bool = False) -> None:
        self._fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._malformed = malformed
        self._hang = hang
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
        if self._hang or frame[0] == 6:
            return
        if frame[0] == 1:
            await self.replies.put(json.dumps([2, 1, {}]))
        elif frame[0] == 64:
            await self.replies.put(json.dumps([65, frame[1], 100 + len(self.sent)]))
        elif frame[0] == 32:
            await self.replies.put(json.dumps([33, frame[1], 200]))
        elif frame[0] == 48:
            envelope: object = {
                "format": "json",
                "messageType": "RESULT",
                "records": [],
                "version": "2",
            }
            if self._malformed:
                envelope = {"private_value": "must-never-appear"}
            elif frame[3] == "/sports#initialDump":
                envelope = {**envelope, "records": self._fixture}
            await self.replies.put(json.dumps([50, frame[1], {}, [envelope]]))

    async def recv(self) -> str:
        return await self.replies.get()

    async def close(self) -> None:
        self.closed = True


class BrokenConnection(ScriptedConnection):
    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        raise WebSocketException("private-token-must-never-appear")


@pytest.mark.asyncio
async def test_collector_uses_source_e_public_contract_and_returns_typed_batch() -> None:
    """A wrong endpoint, WAMP lifecycle, or parser wiring would make the public snapshot unusable."""
    connection = ScriptedConnection()
    seen_config: object | None = None

    def factory(config: object) -> ScriptedConnection:
        nonlocal seen_config
        seen_config = config
        return connection

    batch = await SourceECollector(
        endpoint_config=example_endpoint(Source.SOURCE_E),
        connection_factory=factory,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    ).collect()

    assert seen_config is not None
    assert seen_config.websocket_url == "wss://source-e.example.test/v2"
    assert seen_config.origin == "https://source-e.example.test"
    assert seen_config.realm == "realm-source_e"
    assert seen_config.operator_id == 1003
    assert seen_config.source is Source.SOURCE_E
    assert batch.source is Source.SOURCE_E
    assert batch.started_at == STARTED_AT
    assert batch.finished_at == FINISHED_AT
    assert len(batch.events) == 1
    assert batch.events[0].quotes[Outcome.HOME].odds == Decimal("1.87")
    assert batch.events[0].quotes[Outcome.DRAW].odds == Decimal("3.80")
    assert batch.events[0].quotes[Outcome.AWAY].odds == Decimal("4.15")
    assert connection.closed
    assert connection.sent[0][0:2] == [1, "realm-source_e"]
    assert [frame[3] for frame in connection.sent if frame[0] == 64] == [
        "/sports/1003/tr/custom-events",
        "/sports/1003/tr/custom-sports",
        "/sports/1003/tr/disciplines/BOTH/BOTH",
        "/sports/1003/tr/live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
        "/sports/1003/tr/next-locations-by-popularity-with-live/1/180",
        "/sports/1003/tr/next-sports-by-popularity-with-live/180",
        "/sports/1003/tr/time-ranges-discipline/1/180",
    ]
    assert [frame[3] for frame in connection.sent if frame[0] == 32] == list(SUBSCRIBE_TOPICS)
    calls = [frame[3] for frame in connection.sent if frame[0] == 48]
    assert calls[:2] == ["/sports#getSessionInfo", "/sports#configureFonts"]
    assert calls[2:-1] == ["/sports#initialDump"] * 9
    assert calls[-1] == "/sports#marketGroupsOverview"


@pytest.mark.asyncio
async def test_collector_sanitizes_malformed_public_envelope() -> None:
    """Letting a raw incompatible envelope escape could disclose public-session data."""
    connection = ScriptedConnection(malformed=True)

    with pytest.raises(SourceEUnavailable, match="invalid public response") as caught:
        await SourceECollector(
            connection_factory=lambda _config: connection,
            timeout_seconds=1,
            clock=clock_values(STARTED_AT),
        ).collect()

    assert "must-never-appear" not in str(caught.value)
    assert "private_value" not in str(caught.value)
    assert connection.closed


@pytest.mark.asyncio
async def test_collector_translates_public_timeout() -> None:
    """A stalled anonymous router must become a source-local unavailable result."""
    connection = ScriptedConnection(hang=True)

    with pytest.raises(SourceEUnavailable, match="collection timed out"):
        await SourceECollector(
            connection_factory=lambda _config: connection,
            timeout_seconds=0.01,
            clock=clock_values(STARTED_AT),
        ).collect()

    assert connection.closed


@pytest.mark.asyncio
async def test_collector_sanitizes_transport_failure() -> None:
    """A public transport error must remain local to Source E and never echo peer details."""
    connection = BrokenConnection()

    with pytest.raises(SourceEUnavailable, match="public source unavailable") as caught:
        await SourceECollector(
            connection_factory=lambda _config: connection,
            timeout_seconds=1,
            clock=clock_values(STARTED_AT),
        ).collect()

    assert "private-token-must-never-appear" not in str(caught.value)
    assert "private-token-must-never-appear" not in "".join(
        traceback.format_exception(caught.value)
    )
    assert connection.closed


@pytest.mark.asyncio
async def test_collector_registers_and_queries_tournament_dump_topics() -> None:
    connection = ScriptedConnection()

    batch = await SourceECollector(
        endpoint_config=example_endpoint(Source.SOURCE_E),
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
        tournament_ids=("910000000000000109",),
    ).collect()

    assert batch.source is Source.SOURCE_E
    registrations = [frame[3] for frame in connection.sent if frame[0] == 64]
    assert (
        "/sports/1003/tr/tournament-aggregator-main/910000000000000109/default-event-info/BOTH/4"
        in registrations
    )
    calls = [frame[3] for frame in connection.sent if frame[0] == 48]
    assert calls.count("/sports#initialDump") == 10

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
from surebet.sources.source_c.collector import SourceCCollector, SourceCUnavailable
from surebet.sources.wamp_feed.contracts import SUBSCRIBE_TOPICS
from tests.conftest import example_endpoint

FIXTURE = Path("tests/fixtures/sources/wamp_feed/source_c_football_1x2_v2.json")
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


class DroppedReceiveConnection(ScriptedConnection):
    """Accept HELLO, then drop the transport while REGISTER is pending."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error
        self._drop_next_receive = False

    async def send(self, message: str) -> None:
        frame = json.loads(message)
        self.sent.append(frame)
        if frame[0] == 1:
            await self.replies.put(json.dumps([2, 1, {}]))
        elif frame[0] == 64:
            self._drop_next_receive = True
            await self.replies.put("drop")

    async def recv(self) -> str:
        message = await super().recv()
        if self._drop_next_receive and message == "drop":
            raise self._error
        return message


@pytest.mark.asyncio
async def test_collector_uses_source_c_public_contract_and_returns_typed_batch() -> None:
    """A wrong endpoint, WAMP lifecycle, or parser wiring would make the public snapshot unusable."""
    connection = ScriptedConnection()
    seen_config: object | None = None

    def factory(config: object) -> ScriptedConnection:
        nonlocal seen_config
        seen_config = config
        return connection

    batch = await SourceCCollector(
        endpoint_config=example_endpoint(Source.SOURCE_C),
        connection_factory=factory,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    ).collect()

    assert seen_config is not None
    assert seen_config.websocket_url == "wss://source-c.example.test/v2"
    assert seen_config.origin == "https://source-c.example.test"
    assert seen_config.realm == "realm-source_c"
    assert seen_config.operator_id == 1001
    assert seen_config.source is Source.SOURCE_C
    assert batch.source is Source.SOURCE_C
    assert batch.started_at == STARTED_AT
    assert batch.finished_at == FINISHED_AT
    assert len(batch.events) == 1
    assert batch.events[0].quotes[Outcome.DRAW].odds == Decimal("3.30")
    assert connection.closed
    assert connection.sent[0][0:2] == [1, "realm-source_c"]
    assert [frame[3] for frame in connection.sent if frame[0] == 64] == [
        "/sports/1001/tr/custom-events",
        "/sports/1001/tr/custom-sports",
        "/sports/1001/tr/disciplines/BOTH/BOTH",
        "/sports/1001/tr/live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
        "/sports/1001/tr/next-locations-by-popularity-with-live/1/180",
        "/sports/1001/tr/next-sports-by-popularity-with-live/180",
        "/sports/1001/tr/time-ranges-discipline/1/180",
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

    with pytest.raises(SourceCUnavailable, match="invalid public response") as caught:
        await SourceCCollector(
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

    with pytest.raises(SourceCUnavailable, match="collection timed out"):
        await SourceCCollector(
            connection_factory=lambda _config: connection,
            timeout_seconds=0.01,
            clock=clock_values(STARTED_AT),
        ).collect()

    assert connection.closed


@pytest.mark.asyncio
async def test_collector_sanitizes_transport_failure() -> None:
    """A public transport error must remain local to Source C and never echo peer details."""
    connection = BrokenConnection()

    with pytest.raises(SourceCUnavailable, match="public source unavailable") as caught:
        await SourceCCollector(
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
@pytest.mark.parametrize(
    "receive_error",
    [
        OSError("private-token-must-never-appear"),
        WebSocketException("private-token-must-never-appear"),
    ],
)
async def test_collector_translates_pending_receive_transport_loss(
    receive_error: Exception,
) -> None:
    """Cancelling a pending REGISTER future would leak CancelledError outside the source boundary."""
    connection = DroppedReceiveConnection(receive_error)

    with pytest.raises(SourceCUnavailable) as caught:
        await SourceCCollector(
            connection_factory=lambda _config: connection,
            timeout_seconds=1,
            clock=clock_values(STARTED_AT),
        ).collect()

    assert "private-token-must-never-appear" not in str(caught.value)
    assert connection.closed


@pytest.mark.asyncio
async def test_collector_normalizes_offset_batch_timestamps_to_utc() -> None:
    """Exporting local-offset batch timestamps would make collection output timezone-dependent."""
    offset = datetime.fromisoformat("2026-08-24T10:00:00+03:00").tzinfo
    assert offset is not None
    connection = ScriptedConnection()
    batch = await SourceCCollector(
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(
            datetime(2026, 8, 24, 10, 0, tzinfo=offset),
            datetime(2026, 8, 24, 10, 0, 2, tzinfo=offset),
        ),
    ).collect()

    assert batch.started_at == STARTED_AT
    assert batch.finished_at == FINISHED_AT
    assert batch.started_at.tzinfo is UTC
    assert batch.finished_at.tzinfo is UTC
    assert batch.started_at.utcoffset() == UTC.utcoffset(batch.started_at)
    assert batch.finished_at.utcoffset() == UTC.utcoffset(batch.finished_at)


class DisconnectAfterBootstrapConnection(ScriptedConnection):
    """Complete calls then drop while the collector waits for its subscribed EVENT."""

    async def send(self, message: str) -> None:
        frame = json.loads(message)
        self.sent.append(frame)
        if frame[0] == 1:
            await self.replies.put(json.dumps([2, 1, {}]))
        elif frame[0] == 64:
            await self.replies.put(json.dumps([65, frame[1], 100]))
        elif frame[0] == 32:
            await self.replies.put(json.dumps([33, frame[1], 200]))
        elif frame[0] == 48:
            envelope = {"format": "json", "messageType": "RESULT", "records": [], "version": "2"}
            await self.replies.put(json.dumps([50, frame[1], {}, [envelope]]))
            if frame[3] == "/sports#marketGroupsOverview":
                await self.replies.put("disconnect")

    async def recv(self) -> str:
        reply = await super().recv()
        if reply == "disconnect":
            raise OSError("private-token-must-never-appear")
        return reply


@pytest.mark.asyncio
async def test_collector_fails_promptly_when_transport_drops_after_bootstrap() -> None:
    """The post-bootstrap EVENT wait must end on disconnect instead of consuming its timeout."""
    connection = DisconnectAfterBootstrapConnection()

    with pytest.raises(SourceCUnavailable, match="invalid public response") as caught:
        await asyncio.wait_for(
            SourceCCollector(
                connection_factory=lambda _config: connection,
                timeout_seconds=1,
                clock=clock_values(STARTED_AT),
            ).collect(),
            timeout=0.1,
        )

    assert "private-token-must-never-appear" not in "".join(
        traceback.format_exception(caught.value)
    )


@pytest.mark.asyncio
async def test_collector_registers_and_queries_tournament_dump_topics() -> None:
    connection = ScriptedConnection()

    batch = await SourceCCollector(
        endpoint_config=example_endpoint(Source.SOURCE_C),
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
        tournament_ids=("910000000000000109",),
    ).collect()

    assert batch.source is Source.SOURCE_C
    registrations = [frame[3] for frame in connection.sent if frame[0] == 64]
    assert (
        "/sports/1001/tr/tournament-aggregator-main/910000000000000109/default-event-info/BOTH/4"
        in registrations
    )
    calls = [frame[3] for frame in connection.sent if frame[0] == 48]
    assert calls.count("/sports#initialDump") == 10

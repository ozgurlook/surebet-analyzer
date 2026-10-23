import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import pytest

from surebet.domain.enums import Outcome, Source
from surebet.sources.endpoints import SourceEndpointConfig
from surebet.sources.source_a.collector import (
    SourceACollector,
    SourceAProtocolError,
    SourceAUnavailable,
)
from surebet.sources.source_a.protocol import (
    build_basketball_games_query,
    build_basketball_markets_query,
    build_games_query,
    build_markets_query,
    build_ou25_query,
)
from surebet.taxonomy.models import SourceIdentityKind
from tests.conftest import example_endpoint

FIXTURE = Path("tests/fixtures/sources/source_a/public_1x2_response.json")
STARTED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=2)


def clock_values(*values: datetime) -> Any:
    iterator: Iterator[datetime] = iter(values)
    return lambda: next(iterator)


class FakeConnection:
    def __init__(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.games_data = fixture["games_data"]
        self.markets_data = fixture["markets_data"]
        self.sent: list[dict[str, Any]] = []
        self.replies: asyncio.Queue[str] = asyncio.Queue()
        self.pending_gets: list[dict[str, Any]] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def send(self, message: str) -> None:
        parsed = json.loads(message)
        self.sent.append(parsed)
        if parsed["command"] == "request_session":
            await self.replies.put(
                json.dumps({"rid": parsed["rid"], "code": 0, "data": {"sid": "discarded"}})
            )
            return
        if parsed["command"] == "get":
            params = parsed.get("params", {})
            where = params.get("where", {})
            m_where = where.get("market", {})
            m_type = m_where.get("type")
            if m_type == "P1XP2":
                data = self.markets_data
            elif m_type == "OverUnder":
                data = getattr(self, "ou25_data", {})
            elif where.get("competition", {}).get("id") == 7111:
                if "market" in where:
                    data = getattr(self, "bask_markets_data", {})
                else:
                    data = getattr(self, "bask_games_data", {})
            else:
                data = self.games_data
            await self.replies.put(json.dumps({"rid": parsed["rid"], "code": 0, "data": data}))

    async def recv(self) -> str:
        return await self.replies.get()


class FailingConnection(FakeConnection):
    async def send(self, message: str) -> None:
        parsed = json.loads(message)
        self.sent.append(parsed)
        await self.replies.put(
            json.dumps(
                {
                    "rid": parsed["rid"],
                    "code": 2,
                    "data": {"access_token": "must-never-appear"},
                }
            )
        )


class HangingConnection(FakeConnection):
    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_collector_correlates_out_of_order_replies_into_typed_batch() -> None:
    connection = FakeConnection()
    seen_config: SourceEndpointConfig | None = None

    def factory(config: SourceEndpointConfig) -> FakeConnection:
        nonlocal seen_config
        seen_config = config
        return connection

    collector = SourceACollector(
        endpoint_config=example_endpoint(Source.SOURCE_A),
        connection_factory=factory,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    batch = await collector.collect()

    assert seen_config is not None
    assert seen_config.websocket_url == "wss://source-a.example.test/v2"
    assert seen_config.origin == "https://source-a.example.test"
    assert connection.closed
    assert batch.source is Source.SOURCE_A
    assert batch.started_at == STARTED_AT
    assert batch.finished_at == FINISHED_AT
    assert len(batch.events) == 1
    evidence = batch.events[0].source_competition
    assert evidence is not None
    assert evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
    assert evidence.provider == "source_a"
    assert evidence.country_id == "20"
    assert evidence.country_name == "Avrupa"
    assert evidence.country_alias == "Europe"
    assert evidence.competition_id == "18286520"
    assert evidence.competition_name == "UEFA Avrupa Ligi"
    assert batch.events[0].quotes[Outcome.DRAW].odds.as_tuple().exponent == -2
    assert [message["rid"] for message in connection.sent] == ["1", "2", "3", "4", "5", "6"]
    assert connection.sent == [
        {
            "command": "request_session",
            "params": {"language": "tur", "site_id": 1000, "source": 42},
            "rid": "1",
        },
        {"command": "get", "params": build_games_query(), "rid": "2"},
        {"command": "get", "params": build_markets_query(), "rid": "3"},
        {"command": "get", "params": build_ou25_query(), "rid": "4"},
        {"command": "get", "params": build_basketball_games_query(7111), "rid": "5"},
        {"command": "get", "params": build_basketball_markets_query(7111), "rid": "6"},
    ]


@pytest.mark.asyncio
async def test_collector_reports_nonzero_rpc_code_without_payload_data() -> None:
    connection = FailingConnection()
    collector = SourceACollector(
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT),
    )

    with pytest.raises(SourceAProtocolError) as caught:
        await collector.collect()

    message = str(caught.value).casefold()
    assert "code 2" in message
    assert "must-never-appear" not in message
    assert "access_token" not in message
    assert connection.closed


@pytest.mark.asyncio
async def test_collector_applies_one_total_timeout_and_closes_connection() -> None:
    connection = HangingConnection()
    collector = SourceACollector(
        connection_factory=lambda _config: connection,
        timeout_seconds=0.01,
        clock=clock_values(STARTED_AT),
    )

    with pytest.raises(SourceAUnavailable, match="collection timed out"):
        await collector.collect()

    assert connection.closed


@pytest.mark.asyncio
async def test_collector_sanitizes_incompatible_parsed_payload_errors() -> None:
    connection = FakeConnection()
    nested: dict[str, object] = {"private_value": "must-never-appear"}
    for _ in range(40):
        nested = {"node": nested}
    connection.games_data = nested
    collector = SourceACollector(
        connection_factory=lambda _config: connection,
        timeout_seconds=1,
        clock=clock_values(STARTED_AT, FINISHED_AT),
    )

    with pytest.raises(SourceAProtocolError, match="invalid JSON-RPC response") as caught:
        await collector.collect()

    assert "must-never-appear" not in str(caught.value)
    assert "private_value" not in str(caught.value)
    assert connection.closed

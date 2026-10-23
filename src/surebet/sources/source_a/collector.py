"""Anonymous async collector for Source A's public WebSocket JSON-RPC."""

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Origin

from surebet.domain.enums import Source
from surebet.sources.endpoints import (
    SourceEndpointConfig,
    SourceEndpointNotConfigured,
    load_source_endpoints,
)
from surebet.sources.models import CollectionBatch, SourceUnavailable

from .protocol import (
    build_basketball_games_query,
    build_basketball_markets_query,
    build_games_query,
    build_markets_query,
    build_ou25_query,
    parse_basketball_collection,
    parse_collection,
)

_MAX_FRAME_BYTES = 5 * 1024 * 1024


class SourceAUnavailable(SourceUnavailable):
    """The public Source A source could not produce a batch."""


class SourceAProtocolError(SourceAUnavailable):
    """The public JSON-RPC endpoint returned an incompatible response."""


class RpcConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


ConnectionFactory = Callable[[SourceEndpointConfig], AbstractAsyncContextManager[RpcConnection]]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_connection_factory(
    endpoint_config: SourceEndpointConfig,
) -> AbstractAsyncContextManager[RpcConnection]:
    connection = connect(
        endpoint_config.websocket_url,
        origin=Origin(endpoint_config.origin),
        open_timeout=10,
        close_timeout=5,
        max_size=_MAX_FRAME_BYTES,
    )
    return cast(AbstractAsyncContextManager[RpcConnection], connection)


class _RpcClient:
    def __init__(self, connection: RpcConnection) -> None:
        self._connection = connection
        self._next_rid = 1
        self._pending: dict[str, asyncio.Future[object]] = {}
        self._reader = asyncio.create_task(self._read_responses())

    async def request(self, command: str, params: Mapping[str, Any]) -> object:
        rid = str(self._next_rid)
        self._next_rid += 1
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        message = json.dumps(
            {"command": command, "params": dict(params), "rid": rid},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self._connection.send(message)
        except BaseException:
            self._pending.pop(rid, None)
            if not future.done():
                future.cancel()
            raise
        return await future

    async def _read_responses(self) -> None:
        try:
            while True:
                raw = await self._connection.recv()
                if len(raw) > _MAX_FRAME_BYTES:
                    raise SourceAProtocolError("JSON-RPC response exceeded size limit")
                payload = json.loads(raw, parse_float=Decimal)
                if not isinstance(payload, dict):
                    raise SourceAProtocolError("invalid JSON-RPC response")
                rid_value = payload.get("rid")
                if not isinstance(rid_value, (str, int)) or isinstance(rid_value, bool):
                    raise SourceAProtocolError("invalid JSON-RPC response")
                future = self._pending.pop(str(rid_value), None)
                if future is None or future.done():
                    continue
                code = payload.get("code")
                if code != 0:
                    safe_code = (
                        code if isinstance(code, int) and not isinstance(code, bool) else "unknown"
                    )
                    future.set_exception(
                        SourceAProtocolError(f"JSON-RPC request failed with code {safe_code}")
                    )
                    continue
                data = payload.get("data")
                if not isinstance(data, (dict, list)):
                    future.set_exception(SourceAProtocolError("invalid JSON-RPC response"))
                    continue
                future.set_result(data)
        except asyncio.CancelledError:
            raise
        except (
            ArithmeticError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
            WebSocketException,
            SourceAProtocolError,
        ) as error:
            safe_error = (
                error
                if isinstance(error, SourceAProtocolError)
                else SourceAProtocolError("invalid JSON-RPC response")
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(safe_error)
            self._pending.clear()

    async def close(self) -> None:
        self._reader.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


class SourceACollector:
    """Collect one complete public Source A pre-match football 1X2 snapshot."""

    def __init__(
        self,
        *,
        endpoint_config: SourceEndpointConfig | None = None,
        connection_factory: ConnectionFactory = _default_connection_factory,
        timeout_seconds: float = 20.0,
        clock: Clock = _utc_now,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        endpoint = endpoint_config or load_source_endpoints(Source.SOURCE_A)
        if endpoint.source is not Source.SOURCE_A:
            raise ValueError("endpoint source must be SOURCE_A")
        self._endpoint_config = endpoint
        self._connection_factory = connection_factory
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    async def collect(self) -> CollectionBatch:
        started_at = self._clock()
        # JSON-RPC sessions are scoped to the operator's site id (SUREBET_SOURCE_A_OPERATOR_ID).
        site_id = self._endpoint_config.operator_id
        if site_id is None:
            raise SourceEndpointNotConfigured(
                "operator id not configured (SUREBET_SOURCE_A_OPERATOR_ID)"
            )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._connection_factory(self._endpoint_config) as connection:
                    client = _RpcClient(connection)
                    try:
                        await client.request(
                            "request_session",
                            {"language": "tur", "site_id": site_id, "source": 42},
                        )
                        games_task = asyncio.create_task(client.request("get", build_games_query()))
                        markets_task = asyncio.create_task(
                            client.request("get", build_markets_query())
                        )
                        ou25_task = asyncio.create_task(client.request("get", build_ou25_query()))
                        bask_games_task = asyncio.create_task(
                            client.request("get", build_basketball_games_query(7111))
                        )
                        bask_markets_task = asyncio.create_task(
                            client.request("get", build_basketball_markets_query(7111))
                        )
                        (
                            games_data,
                            markets_data,
                            ou25_data,
                            bask_games_data,
                            bask_markets_data,
                        ) = await asyncio.gather(
                            games_task,
                            markets_task,
                            ou25_task,
                            bask_games_task,
                            bask_markets_task,
                        )
                    finally:
                        await client.close()
        except TimeoutError as error:
            raise SourceAUnavailable("Source A collection timed out") from error
        except SourceAProtocolError:
            raise
        except (OSError, WebSocketException) as error:
            raise SourceAUnavailable("Source A public source unavailable") from error

        finished_at = self._clock()
        try:
            fb_events = parse_collection(
                games_data,
                markets_data,
                ou25_data,
                observed_at=finished_at,
            )
            bask_events = parse_basketball_collection(
                bask_games_data,
                bask_markets_data,
                observed_at=finished_at,
                source=Source.SOURCE_A,
            )
            events = (*fb_events, *bask_events)
        except (TypeError, ValueError) as error:
            raise SourceAProtocolError("invalid JSON-RPC response") from error
        return CollectionBatch(
            source=Source.SOURCE_A,
            started_at=started_at,
            finished_at=finished_at,
            events=events,
        )

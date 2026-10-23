"""Shared anonymous WAMP feed snapshot collection lifecycle."""

import asyncio
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast

from websockets.asyncio.client import connect
from websockets.typing import Origin

from surebet.sources.models import CollectionBatch

from .client import WampConnection, WampFeedClient
from .context import (
    WampFeedContext,
    configure_fonts_kwargs,
    get_session_info_kwargs,
    initial_dump_kwargs,
    make_context,
    market_groups_overview_kwargs,
)
from .contracts import (
    SUBSCRIBE_TOPICS,
    register_procedure,
    tournament_dump_topic,
    tournament_registration_suffix,
)
from .models import WampFeedSourceConfig
from .parser import parse_envelope, parse_live_records, parse_v2_records
from .protocol import WampFeedProtocolError
from .state import WampFeedState

ConnectionFactory = Callable[[WampFeedSourceConfig], AbstractAsyncContextManager[WampConnection]]
Clock = Callable[[], datetime]
_MAX_FRAME_BYTES = 5 * 1024 * 1024
INITIAL_DUMP_TOPICS = (
    "/sports/1001/tr/custom-sports",
    "/sports/1001/tr/next-sports-by-popularity-with-live/180",
    "/sports/1001/tr/disciplines/BOTH/BOTH",
    "/sports/1001/tr/custom-events",
    "/sports/1001/tr/next-locations-by-popularity-with-live/1/180",
    "/sports/1001/tr/time-ranges-discipline/1/180",
    "/sports/1001/tr/live-matches-aggregator-groups-overview/1/all-locations/default-event-info/10/2258,2259",
    "/sports/1001/tr/next-matches-aggregator-main/1/0-180/180/4",
    # The provider truncates each prematch list at its own cap, and the two lists are
    # ordered differently, so this topic carries fixtures the main list drops.
    "/sports/1001/tr/popular-matches-aggregator-groups/1/180/4",
)
_LIVE_RECORD_TYPES = frozenset(
    {"MATCH", "MARKET", "OUTCOME", "BETTING_OFFER", "MARKET_OUTCOME_RELATION"}
)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def default_connection_factory(
    config: WampFeedSourceConfig,
) -> AbstractAsyncContextManager[WampConnection]:
    """Open the configured anonymous public WAMP endpoint."""

    connection = connect(
        config.websocket_url,
        origin=cast(Origin, config.origin),
        open_timeout=10,
        close_timeout=5,
        max_size=_MAX_FRAME_BYTES,
    )
    return cast(AbstractAsyncContextManager[WampConnection], connection)


async def collect_snapshot(
    *,
    config: WampFeedSourceConfig,
    registration_suffixes: Sequence[str],
    connection_factory: ConnectionFactory = default_connection_factory,
    timeout_seconds: float,
    clock: Clock = utc_now,
    tournament_ids: Sequence[str] = (),
) -> CollectionBatch:
    """Collect one public WAMP feed snapshot using the fixed source contract."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    started_at = _as_utc(clock())
    state = WampFeedState()
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    bootstrapped = False
    try:
        async with asyncio.timeout_at(deadline):
            connection_manager = connection_factory(config)
            connection = await connection_manager.__aenter__()
            client = WampFeedClient(connection)
            try:
                await client.hello(config.realm)
                for suffix in registration_suffixes:
                    await client.register(register_procedure(config.operator_id, suffix))
                for tid in tournament_ids:
                    with suppress(WampFeedProtocolError):
                        await client.register(
                            register_procedure(
                                config.operator_id, tournament_registration_suffix(tid)
                            )
                        )
                for topic in SUBSCRIBE_TOPICS:
                    await client.subscribe(topic)
                ctx = make_context(config, clock=lambda: started_at)
                await client.call(
                    "/sports#getSessionInfo", kwargs=get_session_info_kwargs(ctx, config.language)
                )
                await client.call("/sports#configureFonts", kwargs=configure_fonts_kwargs(ctx))
                full_records = await _collect_full_records(
                    client, ctx, config, tournament_ids=tournament_ids
                )
                state.apply_full(full_records)
                bootstrapped = True
                await _drain_invocations(client, state, deadline)
            finally:
                exc_type, exc_value, traceback = sys.exc_info()
                try:
                    await _close_client(client, deadline)
                finally:
                    await _exit_connection_manager(
                        connection_manager, deadline, exc_type, exc_value, traceback
                    )
    except TimeoutError:
        if not bootstrapped:
            raise
    finished_at = _as_utc(clock())
    snapshot = state.snapshot()
    if any(record.get("_type") in _LIVE_RECORD_TYPES for record in snapshot):
        events = parse_live_records(snapshot, source=config.source, observed_at=finished_at)
    else:
        events = parse_v2_records(snapshot, source=config.source, observed_at=finished_at)
    return CollectionBatch(
        source=config.source,
        started_at=started_at,
        finished_at=finished_at,
        events=events,
    )


async def _collect_full_records(
    client: WampFeedClient,
    ctx: WampFeedContext,
    config: WampFeedSourceConfig,
    *,
    tournament_ids: Sequence[str] = (),
) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    valid_topics = 0
    for topic in _initial_dump_topics(config.operator_id):
        try:
            envelope = parse_envelope(
                await client.call("/sports#initialDump", kwargs=initial_dump_kwargs(ctx, topic))
            )
        except WampFeedProtocolError:
            continue
        records.extend(envelope.records)
        valid_topics += 1
    for tid in tournament_ids:
        topic = tournament_dump_topic(config.operator_id, tid, language=config.language)
        try:
            envelope = parse_envelope(
                await client.call("/sports#initialDump", kwargs=initial_dump_kwargs(ctx, topic))
            )
        except WampFeedProtocolError:
            continue
        records.extend(envelope.records)
        valid_topics += 1
    try:
        overview = parse_envelope(
            await client.call(
                "/sports#marketGroupsOverview",
                kwargs=market_groups_overview_kwargs(ctx, config.language, "LIVE", "1"),
            )
        )
    except WampFeedProtocolError:
        if valid_topics == 0:
            raise WampFeedProtocolError("WAMP feed snapshot topics all failed") from None
    else:
        records.extend(overview.records)
        valid_topics += 1
    if valid_topics == 0:
        raise WampFeedProtocolError("WAMP feed snapshot topics all failed")
    return records


async def _drain_invocations(client: WampFeedClient, state: WampFeedState, deadline: float) -> None:
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        receiver = asyncio.create_task(client.recv_invocation())
        try:
            done, _ = await asyncio.wait((receiver,), timeout=remaining)
        finally:
            if not receiver.done():
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
        if not done:
            return
        invocation = receiver.result()
        try:
            envelope = parse_envelope(invocation.kwargs)
        except WampFeedProtocolError:
            pass
        else:
            state.apply_delta(envelope.records)
        await client.yield_invocation(invocation, {"status": "ok"})


async def _close_client(client: WampFeedClient, deadline: float) -> None:
    await _run_cleanup_task(client.close(), deadline)


async def _exit_connection_manager(
    connection_manager: AbstractAsyncContextManager[WampConnection],
    deadline: float,
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    traceback: TracebackType | None,
) -> None:
    await _run_cleanup_task(connection_manager.__aexit__(exc_type, exc_value, traceback), deadline)


async def _run_cleanup_task(coroutine: Coroutine[Any, Any, object], deadline: float) -> None:
    task: asyncio.Task[object] = asyncio.Task(
        coroutine, loop=asyncio.get_running_loop(), eager_start=True
    )
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        _cancel_cleanup_task(task)
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        return
    try:
        done, _ = await asyncio.wait((task,), timeout=remaining)
    except asyncio.CancelledError:
        _cancel_cleanup_task(task)
        raise
    if not done:
        _cancel_cleanup_task(task)
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(0)
        return
    task.result()


def _cancel_cleanup_task(task: asyncio.Task[object]) -> None:
    task.cancel()
    task.add_done_callback(_discard_cleanup_result)


def _discard_cleanup_result(task: asyncio.Task[object]) -> None:
    with suppress(asyncio.CancelledError):
        task.result()


def _initial_dump_topics(operator_id: int) -> tuple[str, ...]:
    return tuple(
        topic.replace("/sports/1001/", f"/sports/{operator_id}/", 1)
        for topic in INITIAL_DUMP_TOPICS
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock timestamp must be timezone-aware")
    return value.astimezone(UTC)

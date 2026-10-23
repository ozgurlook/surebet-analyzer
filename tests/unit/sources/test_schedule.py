import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from surebet.sources.schedule import CollectionFailure, watch_collections
from surebet.sources.source_a.collector import SourceAUnavailable


@dataclass
class FakeTime:
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def take(stream: AsyncIterator[object], count: int) -> list[object]:
    values = []
    for _ in range(count):
        values.append(await anext(stream))
    await stream.aclose()
    return values


@pytest.mark.asyncio
async def test_watch_collections_starts_runs_at_thirty_second_boundaries() -> None:
    time = FakeTime()
    starts: list[float] = []

    async def collect() -> str:
        starts.append(time.now)
        time.now += 5
        return f"batch-{len(starts)}"

    stream = watch_collections(
        collect,
        source="source_a",
        interval_seconds=30,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )

    values = await take(stream, 2)

    assert values == ["batch-1", "batch-2"]
    assert starts == [0, 30]


@pytest.mark.asyncio
async def test_watch_collections_skips_overlap_when_collection_exceeds_interval() -> None:
    time = FakeTime()
    active = 0
    max_active = 0
    starts: list[float] = []

    async def collect() -> str:
        nonlocal active, max_active
        starts.append(time.now)
        active += 1
        max_active = max(max_active, active)
        time.now += 35
        active -= 1
        return "batch"

    stream = watch_collections(
        collect,
        source="source_a",
        interval_seconds=30,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )

    await take(stream, 2)

    assert starts == [0, 60]
    assert max_active == 1


@pytest.mark.asyncio
async def test_watch_collections_reports_sanitized_failure_then_continues() -> None:
    time = FakeTime()
    attempts = 0

    async def collect() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SourceAUnavailable("Source A public source unavailable")
        return "recovered"

    stream = watch_collections(
        collect,
        source="source_a",
        interval_seconds=30,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )

    values = await take(stream, 2)

    assert values == [
        CollectionFailure(source="source_a", reason="Source A public source unavailable"),
        "recovered",
    ]
    assert attempts == 2


def test_watch_collections_rejects_interval_below_thirty_seconds() -> None:
    async def collect() -> None:
        return None

    with pytest.raises(ValueError, match="at least 30 seconds"):
        watch_collections(collect, source="source_a", interval_seconds=29)


@pytest.mark.asyncio
async def test_watch_collections_propagates_cancellation_during_wait() -> None:
    sleeping = asyncio.Event()

    async def collect() -> str:
        return "batch"

    async def blocked_sleep(_: float) -> None:
        sleeping.set()
        await asyncio.Future()

    stream = watch_collections(
        collect,
        source="source_a",
        interval_seconds=30,
        monotonic=lambda: 0.0,
        sleep=blocked_sleep,
    )
    assert await anext(stream) == "batch"
    pending = asyncio.create_task(anext(stream))
    await sleeping.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await stream.aclose()

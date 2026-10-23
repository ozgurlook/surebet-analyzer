"""Source-neutral non-overlapping periodic collection."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from .models import SourceUnavailable

Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class CollectionFailure:
    source: str
    reason: str


def watch_collections[T](
    collect: Callable[[], Awaitable[T]],
    *,
    source: str,
    interval_seconds: float = 30,
    monotonic: Monotonic = time.monotonic,
    sleep: Sleep = asyncio.sleep,
) -> AsyncIterator[T | CollectionFailure]:
    """Yield non-overlapping collection results on fixed interval boundaries."""

    if interval_seconds < 30:
        raise ValueError("interval_seconds must be at least 30 seconds")

    async def iterate() -> AsyncIterator[T | CollectionFailure]:
        next_start = monotonic()
        while True:
            try:
                yield await collect()
            except SourceUnavailable as error:
                yield CollectionFailure(source=source, reason=str(error))

            next_start += interval_seconds
            now = monotonic()
            if next_start <= now:
                skipped = int((now - next_start) // interval_seconds) + 1
                next_start += skipped * interval_seconds
            await sleep(max(0.0, next_start - now))

    return iterate()

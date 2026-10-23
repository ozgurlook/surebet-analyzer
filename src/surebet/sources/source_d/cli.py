"""Local one-shot and periodic commands for the Source D public source."""

import asyncio
import json
from collections.abc import Callable
from typing import Annotated

import typer

from surebet.sources.endpoints import SourceEndpointConfig
from surebet.sources.models import CollectionBatch, SourceUnavailable
from surebet.sources.schedule import CollectionFailure, watch_collections

from .collector import SourceDCollector

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def _default_collector_factory(
    endpoint_config: SourceEndpointConfig | None = None,
) -> SourceDCollector:
    return SourceDCollector(endpoint_config=endpoint_config)


_collector_factory: Callable[..., SourceDCollector] = _default_collector_factory


def _json_line(value: CollectionBatch | CollectionFailure) -> str:
    if isinstance(value, CollectionFailure):
        payload: object = {
            "source": value.source,
            "status": "unavailable",
            "reason": value.reason,
        }
    else:
        payload = value.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


async def _collect_once() -> CollectionBatch:
    return await _collector_factory().collect()


@app.command()
def once() -> None:
    """Print one anonymous public Source D collection batch as JSON.

    The endpoint is read from ``SUREBET_SOURCE_D_ENDPOINT``; see ``surebet.sources.endpoints``.
    """

    try:
        value: CollectionBatch | CollectionFailure = asyncio.run(_collect_once())
    except SourceUnavailable as error:
        value = CollectionFailure(source="source_d", reason=str(error))
        typer.echo(_json_line(value))
        raise typer.Exit(code=1) from None
    typer.echo(_json_line(value))


async def _watch(interval_seconds: int) -> None:
    collector = _collector_factory()
    async for value in watch_collections(
        collector.collect,
        source="source_d",
        interval_seconds=interval_seconds,
    ):
        typer.echo(_json_line(value))


@app.command()
def watch(
    interval_seconds: Annotated[int, typer.Option(min=30)] = 30,
) -> None:
    """Print non-overlapping collection snapshots as newline-delimited JSON."""

    asyncio.run(_watch(interval_seconds))


if __name__ == "__main__":
    app()

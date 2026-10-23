"""Local one-shot and periodic commands for anonymous WAMP-feed sources."""

import asyncio
import json
from collections.abc import Callable
from typing import Annotated

import typer

from surebet.domain.enums import Source
from surebet.sources.endpoints import SourceEndpointConfig
from surebet.sources.models import CollectionBatch, SourceUnavailable
from surebet.sources.schedule import CollectionFailure, watch_collections
from surebet.sources.source_b.collector import SourceBCollector
from surebet.sources.source_c.collector import SourceCCollector
from surebet.sources.source_e.collector import SourceECollector
from surebet.taxonomy.tracked import load_tracked_tournament_ids

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)

Collector = SourceCCollector | SourceBCollector | SourceECollector
CollectorFactory = Callable[..., Collector]

_WAMP_FEED_SOURCES = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_E)


def _default_collector_factory(
    source: Source,
    endpoint_config: SourceEndpointConfig | None = None,
) -> Collector:
    tournament_ids = load_tracked_tournament_ids(source)
    if source is Source.SOURCE_C:
        return SourceCCollector(endpoint_config=endpoint_config, tournament_ids=tournament_ids)
    if source is Source.SOURCE_B:
        return SourceBCollector(endpoint_config=endpoint_config, tournament_ids=tournament_ids)
    if source is Source.SOURCE_E:
        return SourceECollector(endpoint_config=endpoint_config, tournament_ids=tournament_ids)
    raise ValueError("source must be source_c, source_b, or source_e")


_collector_factory: CollectorFactory = _default_collector_factory


def _source_from_option(value: str) -> Source:
    try:
        source = Source(value)
    except ValueError as error:
        raise typer.BadParameter("must be 'source_c', 'source_b', or 'source_e'") from error
    if source not in _WAMP_FEED_SOURCES:
        raise typer.BadParameter("must be 'source_c', 'source_b', or 'source_e'")
    return source


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


async def _collect_once(source: Source) -> CollectionBatch:
    return await _collector_factory(source).collect()


@app.command()
def once(source: Annotated[str, typer.Option()]) -> None:
    """Print one anonymous public WAMP feed collection batch as JSON.

    The endpoint is read from ``SUREBET_<SOURCE>_*``; see ``surebet.sources.endpoints``.
    """

    normalized_source = _source_from_option(source)
    try:
        value: CollectionBatch | CollectionFailure = asyncio.run(_collect_once(normalized_source))
    except SourceUnavailable as error:
        value = CollectionFailure(source=normalized_source.value, reason=str(error))
        typer.echo(_json_line(value))
        raise typer.Exit(code=1) from None
    typer.echo(_json_line(value))


async def _watch(source: Source, interval_seconds: int) -> None:
    collector = _collector_factory(source)
    async for value in watch_collections(
        collector.collect,
        source=source.value,
        interval_seconds=interval_seconds,
    ):
        typer.echo(_json_line(value))


@app.command()
def watch(
    source: Annotated[str, typer.Option()],
    interval_seconds: Annotated[int, typer.Option(min=30)] = 30,
) -> None:
    """Print non-overlapping collection snapshots as newline-delimited JSON."""

    asyncio.run(_watch(_source_from_option(source), interval_seconds))


if __name__ == "__main__":
    app()

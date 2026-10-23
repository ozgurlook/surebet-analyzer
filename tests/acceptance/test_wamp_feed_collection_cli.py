import importlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import CollectedEvent, CollectionBatch, SourceUnavailable
from surebet.sources.schedule import CollectionFailure

runner = CliRunner()
OBSERVED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
SECRET_INDICATORS = re.compile(
    r"(?i)(authorization|cookie|set-cookie|password|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?id|bearer|secret|csrf)"
)


def batch(source: Source) -> CollectionBatch:
    event_id = f"{source.value}-30549576"
    event = CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition="UEFA Avrupa Ligi",
        home="Bodo/Glimt",
        away="NEC Nijmegen",
        kickoff=datetime(2026, 8, 25, 19, 0, tzinfo=UTC),
        market_key="football.full_time_result.90m",
        quotes={
            outcome: Quote(
                source=source,
                event_id=event_id,
                outcome=outcome,
                odds=Decimal(odds),
                observed_at=OBSERVED_AT,
            )
            for outcome, odds in (
                (Outcome.HOME, "1.47"),
                (Outcome.DRAW, "4.33"),
                (Outcome.AWAY, "4.60"),
            )
        },
    )
    return CollectionBatch(
        source=source,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        events=(event,),
    )


class SuccessfulCollector:
    def __init__(self, source: Source) -> None:
        self._source = source

    async def collect(self) -> CollectionBatch:
        return batch(self._source)


class FailedCollector:
    async def collect(self) -> CollectionBatch:
        raise SourceUnavailable("WAMP feed public source unavailable")


@pytest.mark.parametrize(
    ("source", "expected_source"), [("source_c", Source.SOURCE_C), ("source_b", Source.SOURCE_B)]
)
def test_once_prints_one_stable_sanitized_collection_batch(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    expected_source: Source,
) -> None:
    """Using any source outside the WAMP-feed sources would select an unsupported adapter."""
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")
    monkeypatch.setattr(cli_module, "_collector_factory", SuccessfulCollector)

    result = runner.invoke(cli_module.app, ["once", "--source", source])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source"] == expected_source.value
    assert payload["started_at"] == "2026-08-24T07:00:00Z"
    assert payload["finished_at"] == "2026-08-24T07:00:00Z"
    assert payload["events"][0]["source_event_id"] == f"{source}-30549576"
    assert set(payload["events"][0]["quotes"]) == {"HOME", "DRAW", "AWAY"}
    assert not SECRET_INDICATORS.search(result.stdout)
    assert result.stdout.count("\n") == 1


def test_once_returns_sanitized_unavailable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaking a collector exception would expose transport details instead of a failure record."""
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")
    monkeypatch.setattr(cli_module, "_collector_factory", lambda _: FailedCollector())

    result = runner.invoke(cli_module.app, ["once", "--source", "source_c"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "reason": "WAMP feed public source unavailable",
        "source": "source_c",
        "status": "unavailable",
    }
    assert not SECRET_INDICATORS.search(result.stdout)


def test_watch_prints_each_snapshot_and_failure_as_one_json_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypassing the scheduled collector stream would lose its normalized failure snapshots."""
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")

    async def finite_watch(*_: object, **__: object) -> AsyncIterator[object]:
        yield batch(Source.SOURCE_B)
        yield CollectionFailure(source="source_b", reason="temporary unavailable")

    monkeypatch.setattr(cli_module, "_collector_factory", SuccessfulCollector)
    monkeypatch.setattr(cli_module, "watch_collections", finite_watch)

    result = runner.invoke(
        cli_module.app, ["watch", "--source", "source_b", "--interval-seconds", "30"]
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["source"] == "source_b"
    assert lines[0]["events"][0]["source_event_id"] == "source_b-30549576"
    assert lines[1] == {
        "reason": "temporary unavailable",
        "source": "source_b",
        "status": "unavailable",
    }
    assert not SECRET_INDICATORS.search(result.stdout)


@pytest.mark.parametrize("source", ["source_a", "other"])
def test_commands_reject_sources_outside_source_c_and_source_b(source: str) -> None:
    """Accepting Source A here would merge two intentionally separate command contracts."""
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")

    result = runner.invoke(cli_module.app, ["once", "--source", source])

    assert result.exit_code == 2
    assert "source_c" in result.output
    assert "source_b" in result.output


def test_watch_rejects_interval_below_thirty_seconds() -> None:
    """A shorter interval would violate the shared non-overlapping schedule contract."""
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")

    result = runner.invoke(
        cli_module.app, ["watch", "--source", "source_c", "--interval-seconds", "29"]
    )

    assert result.exit_code == 2
    assert "30" in result.output


def test_once_reports_missing_endpoint_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_module = importlib.import_module("surebet.sources.wamp_feed.cli")
    monkeypatch.delenv("SUREBET_SOURCE_C_ENDPOINT")

    result = runner.invoke(cli_module.app, ["once", "--source", "source_c"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "unavailable"
    assert "SUREBET_SOURCE_C_ENDPOINT" in payload["reason"]


def test_default_collector_factory_passes_tracked_tournament_ids() -> None:
    from surebet.sources.wamp_feed.cli import _default_collector_factory

    source_b_collector = _default_collector_factory(Source.SOURCE_B)
    assert hasattr(source_b_collector, "_tournament_ids")
    assert "910000000000000109" in source_b_collector._tournament_ids

    source_c_collector = _default_collector_factory(Source.SOURCE_C)
    assert hasattr(source_c_collector, "_tournament_ids")
    assert "910000000000000109" in source_c_collector._tournament_ids

    source_e_collector = _default_collector_factory(Source.SOURCE_E)
    assert hasattr(source_e_collector, "_tournament_ids")
    assert "910000000000000109" in source_e_collector._tournament_ids

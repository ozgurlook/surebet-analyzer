import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import surebet.sources.source_a.cli as cli_module
from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.sources.schedule import CollectionFailure
from surebet.sources.source_a.cli import app
from surebet.sources.source_a.collector import SourceAUnavailable

runner = CliRunner()
OBSERVED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
SECRET_INDICATORS = re.compile(
    r"(?i)(authorization|cookie|set-cookie|password|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?id|bearer|secret|csrf)"
)


def batch() -> CollectionBatch:
    event_id = "30549576"
    event = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id=event_id,
        competition="UEFA Avrupa Ligi",
        home="Bodo/Glimt",
        away="NEC Nijmegen",
        kickoff=datetime(2026, 8, 25, 19, 0, tzinfo=UTC),
        market_key="football.full_time_result.90m",
        quotes={
            outcome: Quote(
                source=Source.SOURCE_A,
                event_id=event_id,
                outcome=outcome,
                odds=odds,
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
        source=Source.SOURCE_A,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        events=(event,),
    )


class SuccessfulCollector:
    async def collect(self) -> CollectionBatch:
        return batch()


class FailedCollector:
    async def collect(self) -> CollectionBatch:
        raise SourceAUnavailable("Source A public source unavailable")


def test_once_prints_one_stable_sanitized_collection_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "_collector_factory", SuccessfulCollector)

    result = runner.invoke(app, ["once"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source"] == "source_a"
    assert payload["events"][0]["source_event_id"] == "30549576"
    assert set(payload["events"][0]["quotes"]) == {"HOME", "DRAW", "AWAY"}
    assert payload["events"][0]["quotes"]["DRAW"]["odds"] == "4.33"
    assert not SECRET_INDICATORS.search(result.stdout)
    assert result.stdout.count("\n") == 1


def test_once_returns_sanitized_unavailable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "_collector_factory", FailedCollector)

    result = runner.invoke(app, ["once"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "reason": "Source A public source unavailable",
        "source": "source_a",
        "status": "unavailable",
    }
    assert not SECRET_INDICATORS.search(result.stdout)


def test_watch_prints_each_batch_and_failure_as_one_json_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finite_watch(*_: object, **__: object) -> AsyncIterator[object]:
        yield batch()
        yield CollectionFailure(source="source_a", reason="temporary unavailable")

    monkeypatch.setattr(cli_module, "_collector_factory", SuccessfulCollector)
    monkeypatch.setattr(cli_module, "watch_collections", finite_watch)

    result = runner.invoke(app, ["watch", "--interval-seconds", "30"])

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["events"][0]["source_event_id"] == "30549576"
    assert lines[1] == {
        "reason": "temporary unavailable",
        "source": "source_a",
        "status": "unavailable",
    }


def test_watch_rejects_interval_below_thirty_seconds() -> None:
    result = runner.invoke(app, ["watch", "--interval-seconds", "29"])

    assert result.exit_code == 2
    assert "30" in result.output


def test_once_reports_missing_endpoint_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUREBET_SOURCE_A_ENDPOINT")

    result = runner.invoke(app, ["once"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "unavailable"
    assert "SUREBET_SOURCE_A_ENDPOINT" in payload["reason"]


def test_json_output_is_ascii_safe_without_losing_unicode_names() -> None:
    original = batch()
    event = original.events[0].model_copy(update={"home": "Legię Warszawa"})
    unicode_batch = original.model_copy(update={"events": (event,)})

    output = cli_module._json_line(unicode_batch)

    assert output.encode("ascii")
    assert json.loads(output)["events"][0]["home"] == "Legię Warszawa"

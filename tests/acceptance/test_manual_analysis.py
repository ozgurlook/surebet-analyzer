from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from surebet.domain.enums import OpportunityClass, Outcome
from surebet.manual_cli import app

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _run(path: Path) -> Any:
    return runner.invoke(app, [str(path)])


def _payload(name: str) -> tuple[bytes, list[dict[str, Any]]]:
    result = _run(FIXTURES / name)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    assert "\n" not in result.stdout[:-1]
    return result.stdout.encode(), json.loads(result.stdout)


def test_standard_fixture_emits_positive_standard_arbitrage() -> None:
    _, opportunities = _payload("standard-arbitrage.json")
    assert opportunities
    standard = [
        item for item in opportunities if item["classification"] == OpportunityClass.STANDARD
    ]
    assert standard
    assert all(
        scenario["standard_profit_cents"] > 0 for item in standard for scenario in item["scenarios"]
    )
    for item in opportunities:
        assert [scenario["outcome"] for scenario in item["scenarios"]] == [
            outcome.value for outcome in Outcome
        ]
        assert all(isinstance(value, int) for value in item["allocation"]["stakes_cents"].values())
        assert all(
            quote["odds"] in {"2.9", "3.6", "3.1"}
            and quote["observed_at"] == "2026-08-24T12:00:00Z"
            for quote in item["quotes"].values()
        )


def test_bonus_fixture_emits_conditional_assumed_opportunity() -> None:
    _, opportunities = _payload("bonus-conditional.json")
    assert opportunities
    assert any(
        item["classification"] == OpportunityClass.BONUS_CONDITIONAL for item in opportunities
    )
    for item in opportunities:
        assert item["minimum_standard_profit_cents"] <= 0
        assert item["minimum_adjusted_profit_cents"] > 0
        assert item["scenarios"]
        assert [scenario["outcome"] for scenario in item["scenarios"]] == [
            outcome.value for outcome in Outcome
        ]


def _assert_serialized_contract(opportunities: list[dict[str, Any]], odds: set[str]) -> None:
    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "stakes_cents":
                    assert isinstance(child, dict) and all(
                        type(item) is int for item in child.values()
                    )
                elif key.endswith("_cents"):
                    assert type(child) is int
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(opportunities)
    for item in opportunities:
        assert all(
            quote["odds"] in odds and quote["observed_at"].endswith("Z")
            for quote in item["quotes"].values()
        )


def test_both_fixtures_have_recursive_money_and_canonical_quote_fields() -> None:
    _, standard = _payload("standard-arbitrage.json")
    _, conditional = _payload("bonus-conditional.json")
    _assert_serialized_contract(standard, {"2.9", "3.6", "3.1"})
    _assert_serialized_contract(conditional, {"1.5", "2"})


def test_repeated_fixture_invocation_is_byte_identical() -> None:
    first, _ = _payload("standard-arbitrage.json")
    second, _ = _payload("standard-arbitrage.json")
    assert first == second


def test_console_script_invocation_is_valid_json() -> None:
    scripts = [
        Path(sys.executable).with_name("analyze-manual.exe"),
        Path(sys.executable).with_name("analyze-manual.cmd"),
        Path(sys.executable).with_name("analyze-manual"),
    ]
    entrypoint = next((path for path in scripts if path.exists()), None)
    assert entrypoint is not None, "run uv sync before acceptance tests to install analyze-manual"
    completed = subprocess.run(
        [str(entrypoint), str(FIXTURES / "standard-arbitrage.json")],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout) is not None
    assert completed.stderr == b""


def test_isolated_import_does_not_load_http_or_browser_modules() -> None:
    code = (
        "import sys; import surebet.manual_cli; "
        "print(sorted(name for name in sys.modules if any(part in name for part in "
        "('httpx', 'playwright', 'browser', 'collector'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


def _owned_artifact_dir() -> Path:
    path = Path(__file__).parents[2] / "artifacts" / f"task7-invalid-{os.getpid()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_equivalent_quote_order_scales_and_offsets_have_identical_bytes() -> None:
    owned = _owned_artifact_dir()
    try:
        source = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
        source["quotes"] = list(reversed(source["quotes"]))
        for quote in source["quotes"]:
            quote["odds"] += "00"
            quote["observed_at"] = "2026-08-24T15:00:00+03:00"
        equivalent = owned / "equivalent.json"
        equivalent.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        first, _ = _payload("standard-arbitrage.json")
        second = _run(equivalent)
        assert second.exit_code == 0, second.output
        assert second.stdout.encode() == first
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_invalid_input_categories_are_stable_and_do_not_leak_payload_or_path() -> None:
    owned = _owned_artifact_dir()
    try:
        cases = {
            "duplicate.json": (b'{"request":{},"request":{},"quotes":[]}', "invalid_json"),
            "nested-duplicate.json": (
                b'{"request":{"budget_cents":1,"now":"2026-08-24T12:00:00Z","accounts":{},"accounts":{}},"quotes":[]}',
                "invalid_json",
            ),
            "escaped-duplicate.json": (
                b'{"request":{},"\\u0072equest":{},"quotes":[]}',
                "invalid_json",
            ),
            "nan.json": (b'{"request":{},"quotes":[NaN]}', "invalid_json"),
            "malformed.json": (b"{", "invalid_json"),
            "numeric-odds.json": (
                b'{"request":{},"quotes":[{"odds":2.9}]}',
                "invalid_analysis_input",
            ),
            "extra.json": (
                b'{"request":{},"quotes":[],"extra":"payload"}',
                "invalid_analysis_input",
            ),
            "mixed-events.json": (
                b'{"request":{"budget_cents":300,"now":"2026-08-24T12:00:00Z","accounts":{"source_a":{"balance_cents":1000,"max_stake_cents":1000},"source_b":{"balance_cents":1000,"max_stake_cents":1000},"source_c":{"balance_cents":1000,"max_stake_cents":1000}}},"quotes":[{"source":"source_a","event_id":"one","outcome":"HOME","odds":"2.90","observed_at":"2026-08-24T12:00:00Z"},{"source":"source_b","event_id":"two","outcome":"DRAW","odds":"3.60","observed_at":"2026-08-24T12:00:00Z"}]}',
                "invalid_analysis_input",
            ),
            "utf8.json": (b"\xff", "invalid_utf8"),
        }
        for name, (contents, category) in cases.items():
            path = owned / name
            path.write_bytes(contents)
            result = _run(path)
            assert result.exit_code == 2
            assert result.stdout == ""
            assert result.stderr == f"{category}\n"
            assert str(path) not in result.stderr
            assert "payload" not in result.stderr
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_oversized_and_nonregular_inputs_have_distinct_safe_categories() -> None:
    owned = _owned_artifact_dir()
    try:
        oversized = owned / "oversized.json"
        oversized.write_bytes(b"{" + (b" " * (5 * 1024 * 1024)) + b"}")
        result = _run(oversized)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "input_too_large\n"
        result = _run(owned)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "input_not_regular\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_valid_empty_quotes_and_exact_inclusive_size() -> None:
    owned = _owned_artifact_dir()
    try:
        base = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
        base["quotes"] = []
        empty = owned / "empty.json"
        empty.write_text(json.dumps(base, separators=(",", ":")), encoding="utf-8")
        result = _run(empty)
        assert result.exit_code == 0
        assert result.stdout == "[]\n"

        padded = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
        padded["quotes"] = [padded["quotes"][0]]
        event = "e"
        padded["quotes"][0]["event_id"] = event
        exact = owned / "exact-limit.json"
        encoded = json.dumps(padded, separators=(",", ":")).encode()
        assert len(encoded) < 5 * 1024 * 1024
        extra = 5 * 1024 * 1024 - len(encoded)
        padded["quotes"][0]["event_id"] = "e" * (len(event) + extra)
        exact.write_bytes(json.dumps(padded, separators=(",", ":")).encode())
        assert exact.stat().st_size == 5 * 1024 * 1024
        result = _run(exact)
        assert result.exit_code == 0
        assert result.stdout == "[]\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_valid_regular_symlink_is_read_from_opened_target() -> None:
    owned = _owned_artifact_dir()
    try:
        link = owned / "standard-link.json"
        try:
            link.symlink_to(FIXTURES / "standard-arbitrage.json")
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"platform cannot create regular-file symlinks: {error}")
        result = _run(link)
        assert result.exit_code == 0
        assert json.loads(result.stdout)
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_missing_and_unreadable_paths_are_safe() -> None:
    owned = _owned_artifact_dir()
    try:
        missing = owned / "does-not-exist.json"
        result = _run(missing)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "input_unreadable\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


@pytest.mark.parametrize("constant", [b"Infinity", b"-Infinity"])
def test_nonfinite_constants_are_invalid_json_with_valid_base(constant: bytes) -> None:
    raw = (FIXTURES / "standard-arbitrage.json").read_bytes().replace(b'"2.90"', constant, 1)
    owned = _owned_artifact_dir()
    try:
        path = owned / "nonfinite.json"
        path.write_bytes(raw)
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_json\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [("odds", "2.90001"), ("observed_at", "2026-08-24"), ("odds", 2.9)],
)
def test_invalid_quote_fields_are_not_masked_by_invalid_request(field: str, value: object) -> None:
    payload = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
    payload["quotes"][0][field] = value
    owned = _owned_artifact_dir()
    try:
        path = owned / "invalid-field.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_analysis_input\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_extra_field_is_rejected_against_valid_base() -> None:
    payload = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
    payload["request"]["extra"] = "payload"
    owned = _owned_artifact_dir()
    try:
        path = owned / "extra-field.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_analysis_input\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_solver_safe_limit_is_invalid_analysis_input() -> None:
    payload = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
    unsupported = 10**15 + 1
    payload["request"]["budget_cents"] = unsupported
    for account in payload["request"]["accounts"].values():
        account["balance_cents"] = unsupported
        account["max_stake_cents"] = unsupported
    owned = _owned_artifact_dir()
    try:
        path = owned / "unsupported-contract.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_analysis_input\n"
        assert str(path) not in result.stderr
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_unexpected_analysis_exception_remains_code_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("internal sentinel")

    monkeypatch.setattr("surebet.manual_cli.find_opportunities", fail)
    result = _run(FIXTURES / "standard-arbitrage.json")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == "analysis_failed\n"


def test_deep_bounded_json_is_invalid_json_without_traceback() -> None:
    owned = _owned_artifact_dir()
    try:
        path = owned / "deep.json"
        path.write_bytes(b"[" * 100_000 + b"]" * 100_000)
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_json\n"
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_lone_surrogate_is_invalid_json_without_leak() -> None:
    payload = json.loads((FIXTURES / "standard-arbitrage.json").read_text(encoding="utf-8"))
    payload["quotes"][0]["event_id"] = "\ud800"
    owned = _owned_artifact_dir()
    try:
        path = owned / "surrogate.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = _run(path)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_json\n"
        assert "surrogate" not in result.stderr
    finally:
        shutil.rmtree(owned, ignore_errors=False)


def test_fifo_is_not_regular_without_blocking() -> None:
    owned = _owned_artifact_dir()
    fifo = owned / "input.fifo"
    try:
        try:
            mkfifo = getattr(os, "mkfifo", None)
            if mkfifo is None:
                pytest.skip("platform has no FIFO support")
            mkfifo(fifo)
        except (AttributeError, NotImplementedError, OSError) as error:
            pytest.skip(f"platform cannot create FIFOs: {error}")
        completed = subprocess.run(
            [sys.executable, "-m", "surebet.manual_cli", str(fifo)],
            capture_output=True,
            timeout=2,
            check=False,
        )
        assert completed.returncode == 2
        assert completed.stdout == b""
        assert completed.stderr == b"input_not_regular\n"
    finally:
        if fifo.exists():
            fifo.unlink()
        shutil.rmtree(owned, ignore_errors=False)

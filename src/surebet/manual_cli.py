"""A bounded, deterministic JSON-in/JSON-out analysis command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from surebet.domain.models import AnalysisRequest, DomainModel, Opportunity, Quote
from surebet.opportunities.service import find_opportunities

MAX_INPUT_BYTES = 5 * 1024 * 1024
_UNSUPPORTED_INPUT_PREFIX = "unsupported CP-SAT input:"


class ManualInput(DomainModel):
    """The intentionally small, frozen input envelope for the manual slice."""

    request: AnalysisRequest
    quotes: tuple[Quote, ...]


class _InputError(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InputError("invalid_json")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    del value
    raise _InputError("invalid_json")


def _reject_surrogates(value: object) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise _InputError("invalid_json")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_surrogates(key)
            _reject_surrogates(child)
        return
    if isinstance(value, list):
        for child in value:
            _reject_surrogates(child)


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _read_input(path: Path) -> bytes:
    fd: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = None
            try:
                file_stat = os.fstat(handle.fileno())
            except OSError as error:
                raise _InputError("input_unreadable") from error
            if not stat.S_ISREG(file_stat.st_mode):
                raise _InputError("input_not_regular")
            file_size = file_stat.st_size
            data = handle.read(MAX_INPUT_BYTES + 1)
            if file_size > MAX_INPUT_BYTES or len(data) > MAX_INPUT_BYTES:
                raise _InputError("input_too_large")
            if file_size != len(data):
                raise _InputError("input_unreadable")
            return data
    except _InputError:
        raise
    except (PermissionError, IsADirectoryError) as error:
        # Windows refuses to open a directory as a binary stream.  No read
        # occurred, so this fallback only distinguishes that non-regular case.
        if _is_directory(path):
            raise _InputError("input_not_regular") from error
        raise _InputError("input_unreadable") from error
    except (OSError, ValueError) as error:
        raise _InputError("input_unreadable") from error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _load_input(path: Path) -> ManualInput:
    raw = _read_input(path)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _InputError("invalid_utf8") from error
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _InputError:
        raise
    except Exception as error:
        raise _InputError("invalid_json") from error
    try:
        _reject_surrogates(decoded)
    except _InputError:
        raise
    except (MemoryError, OverflowError, RecursionError) as error:
        raise _InputError("invalid_json") from error
    try:
        payload = ManualInput.model_validate(decoded)
    except (TypeError, ValueError, ValidationError) as error:
        raise _InputError("invalid_analysis_input") from error
    if len({quote.event_id for quote in payload.quotes}) > 1:
        raise _InputError("invalid_analysis_input")
    return payload


def _json_output(opportunities: tuple[Opportunity, ...]) -> str:
    values = [item.model_dump(mode="json") for item in opportunities]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def analyze(path: Path) -> None:
    """Analyze one local JSON file without network or source discovery."""
    try:
        payload = _load_input(path)
    except _InputError as error:
        typer.echo(error.category, err=True)
        raise typer.Exit(code=2) from None

    try:
        opportunities = find_opportunities(payload.quotes, payload.request)
        output = _json_output(opportunities)
    except ValueError as error:
        # The solver exposes this stable prefix for domain-valid values beyond
        # its documented bounded integer contract. Other ValueErrors remain
        # unexpected analysis failures and must not be hidden as input errors.
        if str(error).startswith(_UNSUPPORTED_INPUT_PREFIX):
            typer.echo("invalid_analysis_input", err=True)
            raise typer.Exit(code=2) from None
        typer.echo("analysis_failed", err=True)
        raise typer.Exit(code=3) from None
    except Exception:  # noqa: BLE001 - stable public CLI boundary
        typer.echo("analysis_failed", err=True)
        raise typer.Exit(code=3) from None

    try:
        typer.echo(output)
    except (OSError, UnicodeError):
        try:
            typer.echo("analysis_failed", err=True)
        except (OSError, UnicodeError):
            pass
        raise typer.Exit(code=3) from None


if __name__ == "__main__":
    app()

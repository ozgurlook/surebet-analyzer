"""Sanitized, one-cycle live smoke check for the country/league taxonomy pipeline.

Runs exactly one anonymous, protocol-compatible collection cycle against the
three supported sources (Source A on JSON-RPC, Sources B and C on WAMP feed),
using the endpoints configured in the environment (see `surebet.sources.endpoints`),
observes taxonomy evidence, builds the verified catalog, purges expired event
snapshots, and prints a sanitized JSON summary.

Safety properties enforced by this script:

- A brand-new, disposable SQLite database is created under a temporary
  directory for every run and is deleted when the run finishes. The
  application's real database is never opened, read, or written.
- A single source failure is reported per-source and never aborts the other
  two source checks (mirrors `CollectorRunner.run_cycle()`'s isolation).
- The printed report may only ever contain: source name, status, event/
  evidence/verified/unmapped counts, one sample country/league display name
  per healthy source, a scrubbed failure reason, and timestamps. It can never
  contain headers, cookies, tokens, URLs with query strings, or a complete
  raw provider response.
- The process exits nonzero only when every source failed, or when a safety
  invariant (unmapped competitions crossing sources, or a non-empty
  Source C/Source B batch exposing no structured `PROVIDER_ID` tournament
  evidence at all) is violated.

Usage: uv run python scripts/smoke_taxonomy.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from surebet.catalog.service import CatalogService
from surebet.domain.enums import Source
from surebet.matching.models import MatchedEvent
from surebet.sources.models import CollectionBatch
from surebet.storage.db import init_db
from surebet.storage.settings import SystemSettingsRepository
from surebet.storage.snapshots import SnapshotRepository, SourceHealth, SourceHealthStatus
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import SourceIdentityKind, TaxonomyStatus
from surebet.web.collector_runner import CollectorRunner

# Report field/order intentionally mirrors "Source A, Source C, Source B" —
# the only three supported feeds.
SUPPORTED_SOURCES: tuple[Source, ...] = (Source.SOURCE_A, Source.SOURCE_C, Source.SOURCE_B)

# Substrings that must never appear anywhere in a printed smoke report or a
# redacted failure reason. Deliberately broad: this is a safety net, not a
# precise credential parser.
_FORBIDDEN_TERMS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "session",
    "token",
    "bearer",
    "password",
    "secret",
    "csrf",
    "credential",
)

_URL_WITH_QUERY_RE = re.compile(r"([a-z][a-z0-9+.-]*://[^\s\"'?]+)\?\S*", re.IGNORECASE)


class SafetyViolation(RuntimeError):
    """A smoke report, or the taxonomy state it summarizes, failed a safety check."""


class SourceSmokeReport(BaseModel):
    """The only fields ever reported for one source's smoke check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    status: Literal["healthy", "failed"]
    event_count: int = 0
    structured_evidence_count: int = 0
    text_derived_evidence_count: int = 0
    verified_count: int = 0
    unmapped_count: int = 0
    sample_country: str | None = None
    sample_league: str | None = None
    error_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SmokeReport(BaseModel):
    """The full sanitized smoke report printed to stdout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_at: datetime
    sources: tuple[SourceSmokeReport, ...]
    healthy_source_count: int
    shared_league_examples: tuple[str, ...]
    purged_event_count: int


def redact_reason(reason: str, limit: int = 200) -> str:
    """Best-effort scrub of a collector failure message before it is reported.

    Strips query strings from any URL and, if the message still contains a
    forbidden term after that, discards it entirely rather than risk leaking
    header/cookie/token content.
    """
    scrubbed = _URL_WITH_QUERY_RE.sub(r"\1", reason).strip()
    lowered = scrubbed.lower()
    if any(term in lowered for term in _FORBIDDEN_TERMS):
        return "collection failed (reason withheld: contained a sensitive term)"
    return scrubbed[:limit]


def assert_report_is_safe(report: SmokeReport) -> None:
    """Defense-in-depth: refuse to emit a report carrying forbidden content."""
    payload = report.model_dump_json()
    lowered = payload.lower()
    for term in _FORBIDDEN_TERMS:
        if term in lowered:
            raise SafetyViolation(f"smoke report contains a forbidden term: {term!r}")
    if _URL_WITH_QUERY_RE.search(payload):
        raise SafetyViolation("smoke report contains a URL with a query string")


def check_unmapped_isolation(matched_events: Iterable[MatchedEvent]) -> None:
    """Unmapped competitions must never merge across sources into one match."""
    for event in matched_events:
        if event.taxonomy_status is TaxonomyStatus.UNMAPPED and len(event.source_events) > 1:
            raise SafetyViolation(
                "an unmapped matched event crossed sources; unmapped competitions "
                "must remain source-isolated until manually linked"
            )


def check_wamp_feed_structured_presence(
    batches: Iterable[CollectionBatch | None],
) -> None:
    """A non-empty current WAMP feed batch must expose some provider identity."""
    for batch in batches:
        if (
            batch is None
            or batch.source not in (Source.SOURCE_C, Source.SOURCE_B)
            or not batch.events
        ):
            continue
        if not any(
            event.source_competition is not None
            and event.source_competition.identity_kind is SourceIdentityKind.PROVIDER_ID
            for event in batch.events
        ):
            raise SafetyViolation(
                f"{batch.source.value} returned events without structured tournament evidence"
            )


def build_source_report(
    source: Source,
    health: SourceHealth | None,
    batch: CollectionBatch | None,
    matched_events: Iterable[MatchedEvent],
) -> SourceSmokeReport:
    """Extract only the sanctioned summary fields for one source."""
    status: Literal["healthy", "failed"] = (
        "healthy"
        if health is not None and health.status is SourceHealthStatus.HEALTHY
        else "failed"
    )
    error_reason = None
    if status == "failed":
        raw_reason = (
            health.error_message
            if health is not None and health.error_message
            else "no successful collection recorded"
        )
        error_reason = redact_reason(raw_reason)

    event_count = 0
    structured_evidence_count = 0
    text_derived_evidence_count = 0
    if batch is not None:
        event_count = len(batch.events)
        for event in batch.events:
            evidence = event.source_competition
            if evidence is None:
                continue
            if evidence.identity_kind is SourceIdentityKind.PROVIDER_ID:
                structured_evidence_count += 1
            elif evidence.identity_kind is SourceIdentityKind.DERIVED_TEXT:
                text_derived_evidence_count += 1

    verified_count = 0
    unmapped_count = 0
    sample_country: str | None = None
    sample_league: str | None = None
    for event in matched_events:
        if source not in event.source_events:
            continue
        if event.taxonomy_status is TaxonomyStatus.VERIFIED:
            verified_count += 1
            if sample_country is None:
                sample_country = event.country_name
            if sample_league is None:
                sample_league = event.league_name
        else:
            unmapped_count += 1

    return SourceSmokeReport(
        source=source,
        status=status,
        event_count=event_count,
        structured_evidence_count=structured_evidence_count,
        text_derived_evidence_count=text_derived_evidence_count,
        verified_count=verified_count,
        unmapped_count=unmapped_count,
        sample_country=sample_country,
        sample_league=sample_league,
        error_reason=error_reason,
        started_at=health.started_at if health is not None else None,
        finished_at=health.finished_at if health is not None else None,
    )


def _shared_league_examples(
    matched_events: Iterable[MatchedEvent], limit: int = 5
) -> tuple[str, ...]:
    """Verified league names that at least two sources agreed on, as evidence."""
    names = {
        event.league_name
        for event in matched_events
        if event.taxonomy_status is TaxonomyStatus.VERIFIED
        and event.league_name is not None
        and len(event.source_events) > 1
    }
    return tuple(sorted(names)[:limit])


def assemble_report(
    generated_at: datetime,
    source_reports: Sequence[SourceSmokeReport],
    matched_events: Iterable[MatchedEvent],
    purged_event_count: int,
) -> SmokeReport:
    """Combine per-source reports into the final sanitized report and self-audit it."""
    healthy_source_count = sum(1 for report in source_reports if report.status == "healthy")
    report = SmokeReport(
        generated_at=generated_at,
        sources=tuple(source_reports),
        healthy_source_count=healthy_source_count,
        shared_league_examples=_shared_league_examples(matched_events),
        purged_event_count=purged_event_count,
    )
    assert_report_is_safe(report)
    return report


async def run_smoke(db_path: Path) -> SmokeReport:
    """Run one anonymous collection cycle against all three sources and report."""
    init_db(db_path)
    snapshot_repo = SnapshotRepository(db_path)
    settings_repo = SystemSettingsRepository(db_path)
    taxonomy_repo = TaxonomyRepository(db_path)

    runner = CollectorRunner(
        snapshot_repo=snapshot_repo,
        settings_repo=settings_repo,
        taxonomy_repo=taxonomy_repo,
    )
    await runner.run_cycle()

    now = datetime.now(UTC)
    purged = snapshot_repo.purge_expired(now)
    purged_event_count = sum(purged.values())

    latest_batches = {
        source: snapshot_repo.get_latest_batch(source) for source in SUPPORTED_SOURCES
    }
    check_wamp_feed_structured_presence(latest_batches.values())

    catalog = CatalogService(snapshot_repo, taxonomy_repo).build(now)
    check_unmapped_isolation(catalog.matched_events)

    health = snapshot_repo.get_source_health()
    source_reports = [
        build_source_report(
            source,
            health.get(source),
            latest_batches[source],
            catalog.matched_events,
        )
        for source in SUPPORTED_SOURCES
    ]

    return assemble_report(now, source_reports, catalog.matched_events, purged_event_count)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="surebet-smoke-") as tmp_dir:
        db_path = Path(tmp_dir) / "smoke.db"
        try:
            report = asyncio.run(run_smoke(db_path))
        except SafetyViolation as error:
            print(json.dumps({"status": "safety_violation", "reason": str(error)}, indent=2))
            return 1
        print(report.model_dump_json(indent=2))
        return 0 if report.healthy_source_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

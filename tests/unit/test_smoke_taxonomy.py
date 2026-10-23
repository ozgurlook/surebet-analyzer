"""Sanitizer and orchestration-logic tests for the sanitized live smoke script.

`scripts/smoke_taxonomy.py` is not part of the installed `surebet` package, so
it is loaded directly from its file path rather than imported by dotted name.
This keeps the test independent of any pytest/pyproject path configuration.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.snapshots import SourceHealth, SourceHealthStatus
from surebet.taxonomy.models import (
    CompetitionKind,
    SourceCompetitionEvidence,
    SourceIdentityKind,
    TaxonomyStatus,
)

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke_taxonomy.py"

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 31, 18, 0, 0, tzinfo=UTC)


def _load_module() -> ModuleType:
    """Load scripts/smoke_taxonomy.py by path, failing loudly if it is absent."""
    if not _SCRIPT_PATH.exists():
        raise ModuleNotFoundError(f"{_SCRIPT_PATH} does not exist")
    spec = importlib.util.spec_from_file_location("smoke_taxonomy", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke_taxonomy = _load_module()


def _quote(source: Source, event_id: str, outcome: Outcome, odds: str) -> Quote:
    return Quote(
        source=source, event_id=event_id, outcome=outcome, odds=Decimal(odds), observed_at=NOW
    )


def _quotes(source: Source, event_id: str) -> dict[Outcome, Quote]:
    return {
        Outcome.HOME: _quote(source, event_id, Outcome.HOME, "1.90"),
        Outcome.DRAW: _quote(source, event_id, Outcome.DRAW, "3.40"),
        Outcome.AWAY: _quote(source, event_id, Outcome.AWAY, "4.10"),
    }


def _event(
    source: Source,
    event_id: str,
    identity_kind: SourceIdentityKind,
    competition_id: str = "comp-1",
) -> CollectedEvent:
    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition="Süper Lig",
        home="Team A",
        away="Team B",
        kickoff=KICKOFF,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=SourceCompetitionEvidence(
            provider=source.value,
            identity_kind=identity_kind,
            competition_id=competition_id,
            competition_name="Süper Lig",
            country_id="TR",
            country_name="Türkiye",
            kind=CompetitionKind.LEAGUE,
        ),
        quotes=_quotes(source, event_id),
    )


def _batch(source: Source, events: tuple[CollectedEvent, ...]) -> CollectionBatch:
    return CollectionBatch(source=source, started_at=NOW, finished_at=NOW, events=events)


def _healthy(source: Source, event_count: int) -> SourceHealth:
    return SourceHealth(
        source=source,
        status=SourceHealthStatus.HEALTHY,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=250,
        event_count=event_count,
        error_message=None,
        last_success_at=NOW,
        updated_at=NOW,
    )


def _failed(source: Source, error_message: str) -> SourceHealth:
    return SourceHealth(
        source=source,
        status=SourceHealthStatus.UNAVAILABLE,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=100,
        event_count=0,
        error_message=error_message,
        last_success_at=None,
        updated_at=NOW,
    )


def _matched_event(
    *,
    matched_id: str,
    status: TaxonomyStatus,
    source_events: dict[Source, str],
    country_name: str | None = "Türkiye",
    league_name: str | None = "Süper Lig",
) -> MatchedEvent:
    quotes: dict[tuple[Source, Outcome], Quote] = {}
    for source, event_id in source_events.items():
        for outcome, quote in _quotes(source, event_id).items():
            quotes[(source, outcome)] = quote
    return MatchedEvent(
        matched_id=matched_id,
        taxonomy_status=status,
        competition="Süper Lig",
        country_id="country:TR" if status is TaxonomyStatus.VERIFIED else None,
        country_name=country_name if status is TaxonomyStatus.VERIFIED else None,
        country_iso_code="TR" if status is TaxonomyStatus.VERIFIED else None,
        league_id="league:00000000-0000-4000-8000-000000000001"
        if status is TaxonomyStatus.VERIFIED
        else None,
        league_name=league_name if status is TaxonomyStatus.VERIFIED else None,
        home="Team A",
        away="Team B",
        kickoff=KICKOFF,
        source_events=source_events,
        quotes=quotes,
    )


# --- Report shape: only the approved fields may exist -----------------------------------


def test_source_smoke_report_declares_only_the_approved_fields() -> None:
    allowed = {
        "source",
        "status",
        "event_count",
        "structured_evidence_count",
        "text_derived_evidence_count",
        "verified_count",
        "unmapped_count",
        "sample_country",
        "sample_league",
        "error_reason",
        "started_at",
        "finished_at",
    }
    assert set(smoke_taxonomy.SourceSmokeReport.model_fields) == allowed


def test_smoke_report_declares_only_the_approved_fields() -> None:
    allowed = {
        "generated_at",
        "sources",
        "healthy_source_count",
        "shared_league_examples",
        "purged_event_count",
    }
    assert set(smoke_taxonomy.SmokeReport.model_fields) == allowed


def test_report_models_forbid_extra_fields() -> None:
    assert smoke_taxonomy.SourceSmokeReport.model_config.get("extra") == "forbid"
    assert smoke_taxonomy.SmokeReport.model_config.get("extra") == "forbid"


# --- build_source_report: extracts only sanctioned data ----------------------------------


def test_build_source_report_counts_structured_and_text_derived_evidence() -> None:
    batch = _batch(
        Source.SOURCE_A,
        (
            _event(Source.SOURCE_A, "evt-1", SourceIdentityKind.PROVIDER_ID),
            _event(Source.SOURCE_A, "evt-2", SourceIdentityKind.PROVIDER_ID),
        ),
    )
    report = smoke_taxonomy.build_source_report(
        Source.SOURCE_A, _healthy(Source.SOURCE_A, 2), batch, ()
    )

    assert report.status == "healthy"
    assert report.event_count == 2
    assert report.structured_evidence_count == 2
    assert report.text_derived_evidence_count == 0
    assert report.error_reason is None


def test_build_source_report_counts_verified_and_unmapped_matches_for_this_source() -> None:
    matched = (
        _matched_event(
            matched_id="m1",
            status=TaxonomyStatus.VERIFIED,
            source_events={Source.SOURCE_A: "evt-1", Source.SOURCE_C: "evt-2"},
        ),
        _matched_event(
            matched_id="m2",
            status=TaxonomyStatus.UNMAPPED,
            source_events={Source.SOURCE_A: "evt-3"},
        ),
    )
    report = smoke_taxonomy.build_source_report(
        Source.SOURCE_A, _healthy(Source.SOURCE_A, 3), None, matched
    )

    assert report.verified_count == 1
    assert report.unmapped_count == 1
    assert report.sample_country == "Türkiye"
    assert report.sample_league == "Süper Lig"


def test_build_source_report_marks_failed_source_with_redacted_reason() -> None:
    health = _failed(Source.SOURCE_B, "connect to wss://feed.example.test/v2?token=abc123 failed")
    report = smoke_taxonomy.build_source_report(Source.SOURCE_B, health, None, ())

    assert report.status == "failed"
    assert report.event_count == 0
    assert report.error_reason is not None
    assert "token=abc123" not in report.error_reason
    assert "?" not in report.error_reason


def test_build_source_report_redacts_wss_query_string_without_a_forbidden_term() -> None:
    # "sid" is deliberately not one of the nine forbidden terms, so this proves
    # the URL-with-query-string rule itself strips the query string on a wss://
    # URL, rather than the separate forbidden-term blacklist doing the work.
    health = _failed(Source.SOURCE_B, "connect to wss://feed.example.test/v2?sid=abc123 failed")
    report = smoke_taxonomy.build_source_report(Source.SOURCE_B, health, None, ())

    assert report.status == "failed"
    assert report.error_reason is not None
    assert "sid=abc123" not in report.error_reason
    assert "?" not in report.error_reason


def test_build_source_report_never_carries_raw_event_identifiers_or_odds() -> None:
    batch = _batch(
        Source.SOURCE_C,
        (_event(Source.SOURCE_C, "super-secret-event-id-42", SourceIdentityKind.DERIVED_TEXT),),
    )
    report = smoke_taxonomy.build_source_report(
        Source.SOURCE_C, _healthy(Source.SOURCE_C, 1), batch, ()
    )

    dumped = report.model_dump_json()
    assert "super-secret-event-id-42" not in dumped
    assert "1.90" not in dumped  # a quoted price must never appear in the sanitized report


# --- redact_reason: scrubs collector failure text before it is reported ------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abc.def.ghi rejected",
        "Set-Cookie: sid=deadbeef; Path=/",
        "session token expired",
        "https://source-a.example.test/sports?session=xyz timed out",
    ],
)
def test_redact_reason_strips_sensitive_terms(raw: str) -> None:
    scrubbed = smoke_taxonomy.redact_reason(raw)
    lowered = scrubbed.lower()
    for term in ("authorization", "cookie", "session", "token", "bearer"):
        assert term not in lowered


def test_redact_reason_removes_query_strings_from_urls() -> None:
    scrubbed = smoke_taxonomy.redact_reason("GET https://example.com/sports?id=1&x=2 -> timeout")
    assert "?" not in scrubbed
    assert "https://example.com/sports" in scrubbed


def test_redact_reason_removes_query_strings_from_wss_urls() -> None:
    # All three supported sources speak wss://, not https://; "sid" is not one
    # of the nine forbidden terms, so only the URL-with-query-string rule can
    # be responsible for stripping it here.
    scrubbed = smoke_taxonomy.redact_reason(
        "connect to wss://feed.example.com/v2?sid=abc123 failed"
    )
    assert "?" not in scrubbed
    assert "sid=abc123" not in scrubbed
    assert "wss://feed.example.com/v2" in scrubbed


def test_redact_reason_passes_through_benign_messages() -> None:
    scrubbed = smoke_taxonomy.redact_reason("connection refused after 25.0s")
    assert scrubbed == "connection refused after 25.0s"


# --- assert_report_is_safe: the final defense-in-depth gate --------------------------------


def test_assert_report_is_safe_accepts_a_clean_report() -> None:
    report = smoke_taxonomy.SmokeReport(
        generated_at=NOW,
        sources=(
            smoke_taxonomy.SourceSmokeReport(
                source=Source.SOURCE_A,
                status="healthy",
                event_count=10,
                structured_evidence_count=10,
                text_derived_evidence_count=0,
                verified_count=4,
                unmapped_count=6,
                sample_country="Türkiye",
                sample_league="Süper Lig",
                error_reason=None,
                started_at=NOW,
                finished_at=NOW,
            ),
        ),
        healthy_source_count=1,
        shared_league_examples=("Süper Lig",),
        purged_event_count=0,
    )
    smoke_taxonomy.assert_report_is_safe(report)


def test_assert_report_is_safe_rejects_a_forbidden_term_in_error_reason() -> None:
    report = smoke_taxonomy.SmokeReport(
        generated_at=NOW,
        sources=(
            smoke_taxonomy.SourceSmokeReport(
                source=Source.SOURCE_B,
                status="failed",
                error_reason="request failed: Authorization header rejected",
                started_at=NOW,
                finished_at=NOW,
            ),
        ),
        healthy_source_count=0,
        shared_league_examples=(),
        purged_event_count=0,
    )
    with pytest.raises(smoke_taxonomy.SafetyViolation):
        smoke_taxonomy.assert_report_is_safe(report)


def test_assert_report_is_safe_rejects_a_url_with_a_query_string() -> None:
    report = smoke_taxonomy.SmokeReport(
        generated_at=NOW,
        sources=(
            smoke_taxonomy.SourceSmokeReport(
                source=Source.SOURCE_C,
                status="failed",
                error_reason="unreachable: https://source-c.example.test/sports?debug=1",
                started_at=NOW,
                finished_at=NOW,
            ),
        ),
        healthy_source_count=0,
        shared_league_examples=(),
        purged_event_count=0,
    )
    with pytest.raises(smoke_taxonomy.SafetyViolation):
        smoke_taxonomy.assert_report_is_safe(report)


def test_assert_report_is_safe_rejects_a_wss_url_with_a_query_string() -> None:
    # All three supported sources connect over wss://; "sid" is not one of the
    # nine forbidden terms, so only the URL-with-query-string rule can catch
    # this leak through the final whole-payload safety gate.
    report = smoke_taxonomy.SmokeReport(
        generated_at=NOW,
        sources=(
            smoke_taxonomy.SourceSmokeReport(
                source=Source.SOURCE_B,
                status="failed",
                error_reason="unreachable: wss://feed.example.com/v2?sid=abc123",
                started_at=NOW,
                finished_at=NOW,
            ),
        ),
        healthy_source_count=0,
        shared_league_examples=(),
        purged_event_count=0,
    )
    with pytest.raises(smoke_taxonomy.SafetyViolation):
        smoke_taxonomy.assert_report_is_safe(report)


# --- Safety invariants beyond field-level sanitization --------------------------------------


def test_check_unmapped_isolation_rejects_a_cross_source_unmapped_match() -> None:
    matched = (
        _matched_event(
            matched_id="m1",
            status=TaxonomyStatus.UNMAPPED,
            source_events={Source.SOURCE_C: "evt-1", Source.SOURCE_B: "evt-2"},
            country_name=None,
            league_name=None,
        ),
    )
    with pytest.raises(smoke_taxonomy.SafetyViolation):
        smoke_taxonomy.check_unmapped_isolation(matched)


def test_check_unmapped_isolation_accepts_single_source_unmapped_matches() -> None:
    matched = (
        _matched_event(
            matched_id="m1",
            status=TaxonomyStatus.UNMAPPED,
            source_events={Source.SOURCE_C: "evt-1"},
            country_name=None,
            league_name=None,
        ),
    )
    smoke_taxonomy.check_unmapped_isolation(matched)


def test_check_wamp_feed_structured_presence_accepts_provider_evidence() -> None:
    batches = (
        _batch(
            Source.SOURCE_C,
            (_event(Source.SOURCE_C, "evt-1", SourceIdentityKind.PROVIDER_ID),),
        ),
        _batch(
            Source.SOURCE_B,
            (_event(Source.SOURCE_B, "evt-2", SourceIdentityKind.PROVIDER_ID),),
        ),
    )

    smoke_taxonomy.check_wamp_feed_structured_presence(batches)


def test_check_wamp_feed_structured_presence_rejects_all_derived_nonempty_batch() -> None:
    batches = (
        _batch(
            Source.SOURCE_C,
            (_event(Source.SOURCE_C, "evt-1", SourceIdentityKind.DERIVED_TEXT),),
        ),
    )

    with pytest.raises(smoke_taxonomy.SafetyViolation, match="source_c"):
        smoke_taxonomy.check_wamp_feed_structured_presence(batches)


def test_check_wamp_feed_structured_presence_allows_empty_or_missing_batch() -> None:
    smoke_taxonomy.check_wamp_feed_structured_presence((_batch(Source.SOURCE_B, ()), None))


# --- assemble_report: aggregates per-source reports into the final sanitized report --------


def test_assemble_report_counts_healthy_sources_and_shared_leagues() -> None:
    matched = (
        _matched_event(
            matched_id="m1",
            status=TaxonomyStatus.VERIFIED,
            source_events={Source.SOURCE_A: "evt-1", Source.SOURCE_C: "evt-2"},
        ),
    )
    source_reports = [
        smoke_taxonomy.build_source_report(
            Source.SOURCE_A, _healthy(Source.SOURCE_A, 1), None, matched
        ),
        smoke_taxonomy.build_source_report(
            Source.SOURCE_C, _healthy(Source.SOURCE_C, 1), None, matched
        ),
        smoke_taxonomy.build_source_report(
            Source.SOURCE_B, _failed(Source.SOURCE_B, "timeout"), None, ()
        ),
    ]

    report = smoke_taxonomy.assemble_report(NOW, source_reports, matched, purged_event_count=3)

    assert report.healthy_source_count == 2
    assert report.shared_league_examples == ("Süper Lig",)
    assert report.purged_event_count == 3
    assert len(report.sources) == 3

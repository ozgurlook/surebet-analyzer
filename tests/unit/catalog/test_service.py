"""Tests for CatalogService as the sole matched-event construction entry point."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from surebet.catalog.service import CatalogService, CatalogSnapshot
from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch
from surebet.storage.db import init_db
from surebet.storage.snapshots import SnapshotRepository
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import (
    CompetitionKind,
    MappingMethod,
    SourceCompetitionEvidence,
    SourceIdentityKind,
    TaxonomyStatus,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
KICKOFF = NOW + timedelta(days=1)


def _quote(source: Source, event_id: str, outcome: Outcome, odds: str) -> Quote:
    return Quote(
        source=source, event_id=event_id, outcome=outcome, odds=Decimal(odds), observed_at=NOW
    )


def _evidence(
    source: Source,
    competition_id: str,
    competition_name: str,
    country_name: str,
    kind: CompetitionKind = CompetitionKind.LEAGUE,
) -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider=source.value,
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id=competition_id,
        competition_name=competition_name,
        country_name=country_name,
        kind=kind,
    )


def _event(
    source: Source,
    event_id: str,
    competition: str,
    odds: tuple[str, str, str],
    evidence: SourceCompetitionEvidence | None,
) -> CollectedEvent:
    return CollectedEvent(
        source=source,
        source_event_id=event_id,
        competition=competition,
        home="Home FC",
        away="Away FC",
        kickoff=KICKOFF,
        market_key=FULL_TIME_RESULT_MARKET,
        source_competition=evidence,
        quotes={
            Outcome.HOME: _quote(source, event_id, Outcome.HOME, odds[0]),
            Outcome.DRAW: _quote(source, event_id, Outcome.DRAW, odds[1]),
            Outcome.AWAY: _quote(source, event_id, Outcome.AWAY, odds[2]),
        },
    )


@pytest.fixture
def repos(tmp_path: Path) -> tuple[SnapshotRepository, TaxonomyRepository]:
    db_path = tmp_path / "catalog.db"
    init_db(db_path)
    return SnapshotRepository(db_path), TaxonomyRepository(db_path)


def test_build_matches_unknown_kind_provider_ids_through_shared_canonical_league(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    evidence_source_c = _evidence(
        Source.SOURCE_C, "source_c-tournament-41", "Süper Lig", "Türkiye", CompetitionKind.UNKNOWN
    )
    evidence_source_b = _evidence(
        Source.SOURCE_B, "source_b-tournament-93", "Süper Lig", "Türkiye", CompetitionKind.UNKNOWN
    )
    taxonomy_repo.observe(Source.SOURCE_C, evidence_source_c, NOW)
    taxonomy_repo.observe(Source.SOURCE_B, evidence_source_b, NOW)

    verified = taxonomy_repo.build_verified_index()
    assert (
        verified[(Source.SOURCE_C, "source_c-tournament-41")].league.league_id
        == verified[(Source.SOURCE_B, "source_b-tournament-93")].league.league_id
    )
    assert (
        verified[(Source.SOURCE_C, "source_c-tournament-41")].league.kind is CompetitionKind.UNKNOWN
    )

    source_c_ev = _event(
        Source.SOURCE_C, "m1", "Süper Lig", ("2.00", "3.20", "3.50"), evidence_source_c
    )
    source_b_ev = _event(
        Source.SOURCE_B, "j1", "Süper Lig", ("2.10", "3.10", "3.40"), evidence_source_b
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_ev,)
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_ev,)
        )
    )

    service = CatalogService(snapshot_repo, taxonomy_repo)
    snapshot = service.build(NOW)

    assert isinstance(snapshot, CatalogSnapshot)
    assert len(snapshot.matched_events) == 1
    matched = snapshot.matched_events[0]
    assert matched.taxonomy_status is TaxonomyStatus.VERIFIED
    assert matched.country_name == "Türkiye"
    assert {Source.SOURCE_C, Source.SOURCE_B} <= set(matched.source_events)
    assert snapshot.built_at == NOW


def test_build_includes_unmapped_events_without_a_league(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    # No taxonomy_repo.observe() call at all: the competition has no mapping.
    orphan_evidence = _evidence(Source.SOURCE_A, "orphan", "Mystery Cup", "Nowhereland")
    orphan_event = _event(
        Source.SOURCE_A, "s1", "Mystery Cup", ("2.0", "3.0", "3.0"), orphan_evidence
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(orphan_event,)
        )
    )

    service = CatalogService(snapshot_repo, taxonomy_repo)
    snapshot = service.build(NOW)

    assert len(snapshot.matched_events) == 1
    matched = snapshot.matched_events[0]
    assert matched.taxonomy_status is TaxonomyStatus.UNMAPPED
    assert matched.country_id is None
    assert matched.league_id is None


def test_build_exposes_regions_leagues_and_source_health(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    evidence_source_c = _evidence(Source.SOURCE_C, "sl", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_C, evidence_source_c, NOW)
    source_c_ev = _event(
        Source.SOURCE_C, "m1", "Süper Lig", ("2.00", "3.20", "3.50"), evidence_source_c
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_ev,)
        ),
        duration_ms=50,
    )

    service = CatalogService(snapshot_repo, taxonomy_repo)
    snapshot = service.build(NOW)

    region_ids = {r.region_id for r in snapshot.regions}
    assert "country:TR" in region_ids
    league_names = {league.display_name for league in snapshot.leagues}
    assert "Süper Lig" in league_names
    assert Source.SOURCE_C in snapshot.source_health
    assert snapshot.source_health[Source.SOURCE_C].event_count == 1


def test_build_with_no_batches_returns_an_empty_snapshot(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    service = CatalogService(snapshot_repo, taxonomy_repo)
    snapshot = service.build(NOW)
    assert snapshot.matched_events == ()


def _save_untracked_batches(
    snapshot_repo: SnapshotRepository, taxonomy_repo: TaxonomyRepository
) -> None:
    evidence_source_c = _evidence(
        Source.SOURCE_C, "untracked-source_c", "Süper Lig", "Türkiye", CompetitionKind.UNKNOWN
    )
    evidence_source_b = _evidence(
        Source.SOURCE_B, "untracked-source_b", "Süper Lig", "Türkiye", CompetitionKind.UNKNOWN
    )
    taxonomy_repo.observe(Source.SOURCE_C, evidence_source_c, NOW)
    taxonomy_repo.observe(Source.SOURCE_B, evidence_source_b, NOW)

    source_c_ev = _event(
        Source.SOURCE_C, "m1", "Süper Lig", ("2.00", "3.20", "3.50"), evidence_source_c
    )
    source_b_ev = _event(
        Source.SOURCE_B, "j1", "Süper Lig", ("2.10", "3.10", "3.40"), evidence_source_b
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_ev,)
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_B, started_at=NOW, finished_at=NOW, events=(source_b_ev,)
        )
    )


def test_build_without_tracked_only_returns_every_matched_event(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    _save_untracked_batches(snapshot_repo, taxonomy_repo)

    snapshot = CatalogService(snapshot_repo, taxonomy_repo).build(NOW)

    assert len(snapshot.matched_events) == 1


def test_build_with_tracked_only_drops_untracked_events(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    _save_untracked_batches(snapshot_repo, taxonomy_repo)

    snapshot = CatalogService(snapshot_repo, taxonomy_repo).build(NOW, tracked_only=True)

    assert snapshot.matched_events == ()


def _save_tracked_batches(
    snapshot_repo: SnapshotRepository, taxonomy_repo: TaxonomyRepository
) -> None:
    # Real ids from the packaged tracked registry (tr-super-lig): Source A's provider id
    # and the shared WAMP feed id used by both the SOURCE_C and SOURCE_B sources. Observing
    # these through TaxonomyRepository.observe() links both via the production
    # registry path (MappingMethod.TRACKED_REGISTRY), converging on one canonical league.
    evidence_source_a = _evidence(Source.SOURCE_A, "7101", "Süper Lig", "Türkiye")
    evidence_source_c = _evidence(Source.SOURCE_C, "910000000000000101", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_A, evidence_source_a, NOW)
    taxonomy_repo.observe(Source.SOURCE_C, evidence_source_c, NOW)

    source_a_ev = _event(
        Source.SOURCE_A, "s1", "Süper Lig", ("2.00", "3.20", "3.50"), evidence_source_a
    )
    source_c_ev = _event(
        Source.SOURCE_C, "m1", "Süper Lig", ("2.10", "3.10", "3.40"), evidence_source_c
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A, started_at=NOW, finished_at=NOW, events=(source_a_ev,)
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C, started_at=NOW, finished_at=NOW, events=(source_c_ev,)
        )
    )


def test_build_with_tracked_only_keeps_events_linked_via_tracked_registry(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    _save_tracked_batches(snapshot_repo, taxonomy_repo)

    verified = taxonomy_repo.build_verified_index()
    assert verified[(Source.SOURCE_A, "7101")].method is MappingMethod.TRACKED_REGISTRY
    assert (
        verified[(Source.SOURCE_A, "7101")].league.league_id
        == verified[(Source.SOURCE_C, "910000000000000101")].league.league_id
    )

    snapshot = CatalogService(snapshot_repo, taxonomy_repo).build(NOW, tracked_only=True)

    assert len(snapshot.matched_events) == 1
    matched = snapshot.matched_events[0]
    assert {Source.SOURCE_A, Source.SOURCE_C} <= set(matched.source_events)


def test_build_with_tracked_only_drops_retired_pair_sharing_a_current_league(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    current = _evidence(Source.SOURCE_C, "910000000000000101", "Süper Lig", "Türkiye")
    retired = _evidence(Source.SOURCE_A, "retired-super-lig", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_C, current, NOW)
    taxonomy_repo.observe(Source.SOURCE_A, retired, NOW)

    current_league = taxonomy_repo.build_verified_index()[
        (Source.SOURCE_C, "910000000000000101")
    ].league.league_id
    retired_mapping = taxonomy_repo.get_mapping(Source.SOURCE_A, "retired-super-lig")
    assert retired_mapping is not None
    taxonomy_repo.link_manual(
        Source.SOURCE_A,
        "retired-super-lig",
        current_league,
        retired_mapping.updated_at,
        actor="alice",
    )

    retired_event = _event(
        Source.SOURCE_A, "retired-event", "Süper Lig", ("2.00", "3.20", "3.50"), retired
    )
    current_event = _event(
        Source.SOURCE_C, "current-event", "Süper Lig", ("2.10", "3.10", "3.40"), current
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=NOW,
            finished_at=NOW,
            events=(retired_event,),
        )
    )
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=NOW,
            finished_at=NOW,
            events=(current_event,),
        )
    )

    snapshot = CatalogService(snapshot_repo, taxonomy_repo).build(NOW, tracked_only=True)

    assert len(snapshot.matched_events) == 1
    assert snapshot.matched_events[0].source_events == {Source.SOURCE_C: "current-event"}


def test_catalog_service_caches_snapshot_and_reuses_it(
    repos: tuple[SnapshotRepository, TaxonomyRepository],
) -> None:
    snapshot_repo, taxonomy_repo = repos
    current = _evidence(Source.SOURCE_C, "910000000000000101", "Süper Lig", "Türkiye")
    taxonomy_repo.observe(Source.SOURCE_C, current, NOW)
    event = _event(Source.SOURCE_C, "e1", "Süper Lig", ("2.10", "3.10", "3.40"), current)
    snapshot_repo.save_batch(
        CollectionBatch(
            source=Source.SOURCE_C,
            started_at=NOW,
            finished_at=NOW,
            events=(event,),
        )
    )

    service = CatalogService(snapshot_repo, taxonomy_repo)
    snap1 = service.build(NOW, tracked_only=True)
    snap2 = service.build(NOW + timedelta(seconds=1), tracked_only=True)

    assert snap1.version_tag != ""
    assert snap1.version_tag == snap2.version_tag
    assert snap1.matched_events == snap2.matched_events
    assert snap2.built_at == NOW + timedelta(seconds=1)

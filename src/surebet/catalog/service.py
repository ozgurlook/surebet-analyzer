"""CatalogService: the sole entry point for building matched events plus taxonomy state.

Every web route that needs matched events, canonical regions/leagues, or source
health must call `CatalogService.build()` rather than independently calling
`TaxonomyRepository.build_verified_index()` and `match_events()`. This keeps the
matching/taxonomy pipeline in exactly one place.
"""

import hashlib
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from surebet.domain.enums import Source
from surebet.matching.matcher import match_events
from surebet.matching.models import MatchedEvent
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.storage.snapshots import SnapshotRepository, SourceHealth
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.taxonomy.models import CanonicalLeague, CanonicalRegion, SourceIdentityKind

_LEAGUE_PAGE_SIZE = 200


class CatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched_events: tuple[MatchedEvent, ...]
    regions: tuple[CanonicalRegion, ...]
    leagues: tuple[CanonicalLeague, ...]
    source_health: Mapping[Source, SourceHealth]
    built_at: datetime
    version_tag: str = ""


class CatalogService:
    """Builds a `CatalogSnapshot` from current snapshot batches and the taxonomy."""

    def __init__(
        self, snapshot_repo: SnapshotRepository, taxonomy_repo: TaxonomyRepository
    ) -> None:
        self._snapshot_repo = snapshot_repo
        self._taxonomy_repo = taxonomy_repo
        self._cache: dict[bool, tuple[tuple[object, object], CatalogSnapshot]] = {}

    def invalidate_cache(self) -> None:
        self._cache.clear()

    def build(self, now: datetime, *, tracked_only: bool = False) -> CatalogSnapshot:
        current_sig = (
            self._snapshot_repo.get_snapshots_signature()
            if hasattr(self._snapshot_repo, "get_snapshots_signature")
            else None,
            self._taxonomy_repo.get_mappings_signature()
            if hasattr(self._taxonomy_repo, "get_mappings_signature")
            else None,
        )

        if current_sig[0] is not None and current_sig[1] is not None:
            cached = self._cache.get(tracked_only)
            if cached is not None:
                cached_sig, cached_snapshot = cached
                if cached_sig == current_sig:
                    return CatalogSnapshot(
                        matched_events=cached_snapshot.matched_events,
                        regions=cached_snapshot.regions,
                        leagues=cached_snapshot.leagues,
                        source_health=cached_snapshot.source_health,
                        built_at=now,
                        version_tag=cached_snapshot.version_tag,
                    )

        batches = tuple(
            batch
            for source in Source
            if (batch := self._snapshot_repo.get_latest_batch(source)) is not None
        )
        if tracked_only:
            batches = _filter_to_approved_current_registry_pairs(
                batches, self._taxonomy_repo.approved_current_registry_pairs()
            )
        taxonomy_index = self._taxonomy_repo.build_verified_index()
        matched_events = match_events(batches, taxonomy_index)
        leagues = self._all_leagues()
        if tracked_only:
            tracked = self._taxonomy_repo.tracked_league_ids()
            matched_events = tuple(event for event in matched_events if event.league_id in tracked)
            leagues = tuple(league for league in leagues if league.league_id in tracked)

        version_tag = (
            hashlib.sha256(f"{current_sig}:{tracked_only}".encode()).hexdigest()[:16]
            if current_sig[0] is not None
            else ""
        )

        snapshot = CatalogSnapshot(
            matched_events=matched_events,
            regions=self._taxonomy_repo.list_regions(),
            leagues=leagues,
            source_health=self._snapshot_repo.get_source_health(),
            built_at=now,
            version_tag=version_tag,
        )

        if current_sig[0] is not None and current_sig[1] is not None:
            self._cache[tracked_only] = (current_sig, snapshot)

        return snapshot

    def _all_leagues(self) -> tuple[CanonicalLeague, ...]:
        """Fetch every active canonical league, paging past list_leagues()'s 200 cap."""
        leagues: list[CanonicalLeague] = []
        offset = 0
        while True:
            page = self._taxonomy_repo.list_leagues(limit=_LEAGUE_PAGE_SIZE, offset=offset)
            leagues.extend(page)
            if len(page) < _LEAGUE_PAGE_SIZE:
                break
            offset += _LEAGUE_PAGE_SIZE
        return tuple(leagues)


def _filter_to_approved_current_registry_pairs(
    batches: tuple[CollectionBatch, ...],
    approved_pairs: frozenset[tuple[Source, str]],
) -> tuple[CollectionBatch, ...]:
    return tuple(
        batch.model_copy(
            update={
                "events": tuple(
                    event
                    for event in batch.events
                    if _has_approved_current_registry_pair(event, approved_pairs)
                )
            }
        )
        for batch in batches
    )


def _has_approved_current_registry_pair(
    event: CollectedEvent,
    approved_pairs: frozenset[tuple[Source, str]],
) -> bool:
    evidence = event.source_competition
    return (
        evidence is not None
        and evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
        and (event.source, evidence.competition_id) in approved_pairs
    )

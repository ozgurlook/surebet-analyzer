"""Transactional canonical taxonomy observation and mapping repository."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Row
from typing import NamedTuple, cast
from uuid import uuid4

from pydantic import BaseModel

from surebet.domain.enums import Source
from surebet.taxonomy.models import (
    CanonicalLeague,
    CanonicalRegion,
    CompetitionKind,
    CompetitionMapping,
    CreateLeagueInput,
    LeagueSignature,
    MappingMethod,
    RegionClass,
    ResolvedCompetition,
    ReviewState,
    SourceCompetition,
    SourceCompetitionEvidence,
    SourceIdentityKind,
)
from surebet.taxonomy.normalizer import CountryDictionary, normalize_taxonomy_text
from surebet.taxonomy.tracked import TrackedCompetition, TrackedCompetitionRegistry

from .db import get_connection

_SYSTEM_ACTOR = "system"
"""Audit actor for automated, non-user-initiated taxonomy writes (e.g. observation-time
auto-mapping and tracked-league linking during collection)."""


class TaxonomyConflict(RuntimeError):
    """The record changed after the caller loaded its optimistic version."""


class LeagueState(NamedTuple):
    """A canonical league together with its optimistic-concurrency version."""

    league: CanonicalLeague
    updated_at: datetime


class TaxonomyRepository:
    def __init__(
        self,
        db_path: Path | str,
        tracked_registry: TrackedCompetitionRegistry | None = None,
    ) -> None:
        """`tracked_registry` overrides the packaged curated competition list.

        The tracked scope is now the product's hard boundary rather than a user
        preference, so it has to be injectable: callers that legitimately cover
        a different set of competitions supply their own registry instead of
        widening the catalog at request time.
        """
        self._db_path = db_path
        self._countries = CountryDictionary.load_packaged()
        self._tracked = tracked_registry or TrackedCompetitionRegistry.load_packaged()

    @property
    def db_path(self) -> Path | str:
        return self._db_path

    def observe(
        self,
        source: Source,
        evidence: SourceCompetitionEvidence,
        observed_at: datetime,
    ) -> SourceCompetition:
        source = _source(source)
        observed_at = _aware(observed_at)
        if evidence.provider != source.value:
            raise ValueError("evidence provider must match source")
        timestamp = observed_at.isoformat()
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO source_competitions "
                "(source, competition_id, provider, identity_kind, competition_name, "
                "country_id, country_name, country_alias, season, kind, gender, age_group, "
                "is_reserve, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, competition_id) DO UPDATE SET "
                "provider=excluded.provider, identity_kind=excluded.identity_kind, "
                "competition_name=excluded.competition_name, country_id=excluded.country_id, "
                "country_name=excluded.country_name, country_alias=excluded.country_alias, "
                "season=excluded.season, kind=excluded.kind, gender=excluded.gender, "
                "age_group=excluded.age_group, is_reserve=excluded.is_reserve, "
                "first_seen_at=MIN(source_competitions.first_seen_at, excluded.first_seen_at), "
                "last_seen_at=MAX(source_competitions.last_seen_at, excluded.last_seen_at);",
                (
                    source.value,
                    evidence.competition_id,
                    evidence.provider,
                    evidence.identity_kind.value,
                    evidence.competition_name,
                    evidence.country_id,
                    evidence.country_name,
                    evidence.country_alias,
                    evidence.season,
                    evidence.kind.value,
                    evidence.gender,
                    evidence.age_group,
                    _bool_db(evidence.is_reserve),
                    timestamp,
                    timestamp,
                ),
            )
            if evidence.identity_kind is SourceIdentityKind.PROVIDER_ID and not self._link_tracked(
                conn, source, evidence.competition_id, observed_at
            ):
                self._auto_map(conn, source, evidence, observed_at)
            row = _source_row(conn, source, evidence.competition_id)
        return _source_competition(row)

    def observe_batch(
        self,
        source: Source,
        evidences: Sequence[SourceCompetitionEvidence],
        observed_at: datetime,
    ) -> None:
        if not evidences:
            return
        source = _source(source)
        observed_at = _aware(observed_at)
        timestamp = observed_at.isoformat()

        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            for evidence in evidences:
                if evidence.provider != source.value:
                    raise ValueError("evidence provider must match source")
                conn.execute(
                    "INSERT INTO source_competitions "
                    "(source, competition_id, provider, identity_kind, competition_name, "
                    "country_id, country_name, country_alias, season, kind, gender, age_group, "
                    "is_reserve, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source, competition_id) DO UPDATE SET "
                    "provider=excluded.provider, identity_kind=excluded.identity_kind, "
                    "competition_name=excluded.competition_name, country_id=excluded.country_id, "
                    "country_name=excluded.country_name, country_alias=excluded.country_alias, "
                    "season=excluded.season, kind=excluded.kind, gender=excluded.gender, "
                    "age_group=excluded.age_group, is_reserve=excluded.is_reserve, "
                    "first_seen_at=MIN(source_competitions.first_seen_at, excluded.first_seen_at), "
                    "last_seen_at=MAX(source_competitions.last_seen_at, excluded.last_seen_at);",
                    (
                        source.value,
                        evidence.competition_id,
                        evidence.provider,
                        evidence.identity_kind.value,
                        evidence.competition_name,
                        evidence.country_id,
                        evidence.country_name,
                        evidence.country_alias,
                        evidence.season,
                        evidence.kind.value,
                        evidence.gender,
                        evidence.age_group,
                        _bool_db(evidence.is_reserve),
                        timestamp,
                        timestamp,
                    ),
                )
                if (
                    evidence.identity_kind is SourceIdentityKind.PROVIDER_ID
                    and not self._link_tracked(conn, source, evidence.competition_id, observed_at)
                ):
                    self._auto_map(conn, source, evidence, observed_at)

    def build_verified_index(self) -> dict[tuple[Source, str], ResolvedCompetition]:
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT m.source, m.competition_id, m.method, m.confidence, "
                "r.region_id, r.region_class, r.iso_code, r.display_name AS region_name, "
                "r.normalized_key AS region_key, r.active AS region_active, "
                "l.league_id, l.display_name AS league_name, l.normalized_key AS league_key, "
                "l.kind, l.gender, l.age_group, l.is_reserve, l.active AS league_active "
                "FROM competition_mappings m "
                "JOIN canonical_leagues l ON l.league_id=m.league_id "
                "JOIN canonical_regions r ON r.region_id=l.region_id "
                "WHERE m.review_state=? AND l.active=1 AND r.active=1;",
                (ReviewState.APPROVED.value,),
            ).fetchall()
        return {
            (Source(row["source"]), row["competition_id"]): ResolvedCompetition(
                source=Source(row["source"]),
                competition_id=row["competition_id"],
                region=_region(row),
                league=_league(row),
                method=MappingMethod(row["method"]),
                confidence=row["confidence"],
            )
            for row in rows
        }

    def list_source_competitions(self, limit: int, offset: int) -> tuple[SourceCompetition, ...]:
        _page(limit, offset)
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM source_competitions ORDER BY source, competition_id LIMIT ? OFFSET ?;",
                (limit, offset),
            ).fetchall()
        return tuple(_source_competition(row) for row in rows)

    def list_unmatched(self, limit: int = 100, offset: int = 0) -> tuple[SourceCompetition, ...]:
        _page(limit, offset)
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT s.* FROM source_competitions s LEFT JOIN competition_mappings m "
                "ON m.source=s.source AND m.competition_id=s.competition_id "
                "AND m.review_state=? WHERE m.source IS NULL "
                "ORDER BY s.last_seen_at DESC, s.source, s.competition_id LIMIT ? OFFSET ?;",
                (ReviewState.APPROVED.value, limit, offset),
            ).fetchall()
        return tuple(_source_competition(row) for row in rows)

    def list_mapped(self, limit: int = 100, offset: int = 0) -> tuple[SourceCompetition, ...]:
        """List observations whose mappings are currently approved."""
        _page(limit, offset)
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT s.* FROM source_competitions s JOIN competition_mappings m "
                "ON m.source=s.source AND m.competition_id=s.competition_id "
                "WHERE m.review_state=? "
                "ORDER BY m.updated_at DESC, s.source, s.competition_id LIMIT ? OFFSET ?;",
                (ReviewState.APPROVED.value, limit, offset),
            ).fetchall()
        return tuple(_source_competition(row) for row in rows)

    def tracked_league_ids(self) -> frozenset[str]:
        """Approved league targets for provider ids in the current tracked registry."""
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT source, competition_id, league_id FROM competition_mappings "
                "WHERE review_state=?;",
                (ReviewState.APPROVED.value,),
            ).fetchall()
        return frozenset(
            row["league_id"]
            for row in rows
            if self._tracked.resolve(Source(row["source"]), row["competition_id"]) is not None
        )

    def approved_current_registry_pairs(self) -> frozenset[tuple[Source, str]]:
        """Approved source identities that remain in the current tracked registry."""
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT source, competition_id FROM competition_mappings WHERE review_state=?;",
                (ReviewState.APPROVED.value,),
            ).fetchall()
        return frozenset(
            (Source(row["source"]), row["competition_id"])
            for row in rows
            if self._tracked.resolve(Source(row["source"]), row["competition_id"]) is not None
        )

    def list_regions(self, active_only: bool = True) -> tuple[CanonicalRegion, ...]:
        query = "SELECT * FROM canonical_regions"
        params: tuple[object, ...] = ()
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY normalized_key, region_id;"
        with get_connection(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return tuple(_region(row) for row in rows)

    def list_leagues(
        self,
        region_ids: tuple[str, ...] = (),
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[CanonicalLeague, ...]:
        _page(limit, offset, maximum=200)
        clauses = ["active=1"]
        params: list[object] = []
        if region_ids:
            clauses.append(f"region_id IN ({','.join('?' for _ in region_ids)})")
            params.extend(region_ids)
        params.extend((limit, offset))
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM canonical_leagues WHERE {' AND '.join(clauses)} "
                "ORDER BY normalized_key, league_id LIMIT ? OFFSET ?;",
                tuple(params),
            ).fetchall()
        return tuple(_league(row) for row in rows)

    def get_mapping(self, source: Source, competition_id: str) -> CompetitionMapping | None:
        """Read one mapping, including pending suggestions, with its optimistic version."""
        source = _source(source)
        with get_connection(self._db_path) as conn:
            row = _optional_mapping_row(conn, source, competition_id)
        return None if row is None else _mapping(row)

    def get_league(self, league_id: str) -> LeagueState | None:
        """Read one league, active or not, with its optimistic version."""
        with get_connection(self._db_path) as conn:
            row = cast(
                Row | None,
                conn.execute(
                    "SELECT * FROM canonical_leagues WHERE league_id=?;", (league_id,)
                ).fetchone(),
            )
        if row is None:
            return None
        return LeagueState(_league(row), datetime.fromisoformat(row["updated_at"]))

    def save_mapping(
        self, mapping: CompetitionMapping, actor: str = _SYSTEM_ACTOR
    ) -> CompetitionMapping:
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            before_row = _optional_mapping_row(conn, mapping.source, mapping.competition_id)
            before = _mapping(before_row) if before_row is not None else None
            self._write_mapping(conn, mapping)
            _audit(conn, "save_mapping", before, mapping, mapping.updated_at, actor)
        return mapping

    def approve_suggestion(
        self,
        source: Source,
        competition_id: str,
        expected_updated_at: datetime,
        actor: str,
        note: str | None = None,
    ) -> CompetitionMapping:
        source = _source(source)
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = _mapping_row(conn, source, competition_id)
            _expect(row["updated_at"], expected_updated_at)
            if ReviewState(row["review_state"]) is not ReviewState.PENDING:
                raise ValueError("mapping is not pending")
            self._validate_link(
                _source_row(conn, source, competition_id), _league_row(conn, row["league_id"])
            )
            now = datetime.now(UTC)
            mapping = _mapping(row).model_copy(
                update={"review_state": ReviewState.APPROVED, "note": note, "updated_at": now}
            )
            self._write_mapping(conn, mapping)
            _audit(conn, "approve_suggestion", _mapping(row), mapping, now, actor)
        return mapping

    def link_manual(
        self,
        source: Source,
        competition_id: str,
        league_id: str,
        expected_updated_at: datetime,
        actor: str,
        note: str | None = None,
    ) -> CompetitionMapping:
        source = _source(source)
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            before = _optional_mapping_row(conn, source, competition_id)
            version = (
                before["updated_at"]
                if before is not None
                else _source_row(conn, source, competition_id)["last_seen_at"]
            )
            _expect(version, expected_updated_at)
            league_row = _league_row(conn, league_id)
            self._validate_link(_source_row(conn, source, competition_id), league_row)
            now = datetime.now(UTC)
            mapping = CompetitionMapping(
                source=source,
                competition_id=competition_id,
                league_id=league_id,
                method=MappingMethod.MANUAL,
                confidence=1.0,
                review_state=ReviewState.APPROVED,
                note=note,
                created_at=_mapping(before).created_at if before is not None else now,
                updated_at=now,
            )
            self._write_mapping(conn, mapping)
            _audit(
                conn,
                "link_manual",
                _mapping(before) if before is not None else None,
                mapping,
                now,
                actor,
            )
        return mapping

    def create_and_link(
        self,
        source: Source,
        competition_id: str,
        region_id: str,
        league_input: CreateLeagueInput,
        expected_updated_at: datetime,
        actor: str,
        note: str | None = None,
    ) -> CompetitionMapping:
        source = _source(source)
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if _optional_mapping_row(conn, source, competition_id) is not None:
                raise ValueError("competition already has a mapping")
            source_row = _source_row(conn, source, competition_id)
            _expect(source_row["last_seen_at"], expected_updated_at)
            if (
                conn.execute(
                    "SELECT 1 FROM canonical_regions WHERE region_id=?;", (region_id,)
                ).fetchone()
                is None
            ):
                raise LookupError("region not found")
            now = datetime.now(UTC)
            self._validate_prospective_link(source_row, region_id, league_input)
            league = self._create_league(conn, region_id, league_input, now)
            mapping = CompetitionMapping(
                source=source,
                competition_id=competition_id,
                league_id=league.league_id,
                method=MappingMethod.MANUAL,
                confidence=1.0,
                review_state=ReviewState.APPROVED,
                note=note,
                created_at=now,
                updated_at=now,
            )
            self._write_mapping(conn, mapping)
            _audit(conn, "create_and_link", None, mapping, now, actor)
        return mapping

    def rename_league(
        self,
        league_id: str,
        display_name: str,
        expected_updated_at: datetime,
        actor: str,
    ) -> CanonicalLeague:
        normalized = normalize_taxonomy_text(display_name)
        if not normalized:
            raise ValueError("display_name must be nonempty")
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = _league_row(conn, league_id)
            _expect(row["updated_at"], expected_updated_at)
            before = _league(row)
            now = datetime.now(UTC)
            conn.execute(
                "UPDATE canonical_leagues SET display_name=?, normalized_key=?, updated_at=? "
                "WHERE league_id=?;",
                (display_name, normalized, now.isoformat(), league_id),
            )
            after = before.model_copy(
                update={"display_name": display_name, "normalized_key": normalized}
            )
            _audit(conn, "rename_league", before, after, now, actor)
        return after

    def remove_manual_mapping(
        self,
        source: Source,
        competition_id: str,
        expected_updated_at: datetime,
        actor: str = _SYSTEM_ACTOR,
    ) -> None:
        source = _source(source)
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = _mapping_row(conn, source, competition_id)
            _expect(row["updated_at"], expected_updated_at)
            before = _mapping(row)
            if before.method is not MappingMethod.MANUAL:
                raise ValueError("only manual mappings can be removed")
            conn.execute(
                "DELETE FROM competition_mappings WHERE source=? AND competition_id=?;",
                (source.value, competition_id),
            )
            _audit(conn, "remove_manual_mapping", before, None, datetime.now(UTC), actor)

    def undo_mapping(
        self,
        source: Source,
        competition_id: str,
        expected_updated_at: datetime,
        actor: str,
    ) -> None:
        """Undo a reversible approved mapping while preserving suggestions."""
        source = _source(source)
        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = _mapping_row(conn, source, competition_id)
            _expect(row["updated_at"], expected_updated_at)
            before = _mapping(row)
            now = datetime.now(UTC)
            if (
                before.method is MappingMethod.MANUAL
                and before.review_state is ReviewState.APPROVED
            ):
                conn.execute(
                    "DELETE FROM competition_mappings WHERE source=? AND competition_id=?;",
                    (source.value, competition_id),
                )
                _audit(conn, "undo_manual_mapping", before, None, now, actor)
                return
            if (
                before.method is MappingMethod.SUGGESTION
                and before.review_state is ReviewState.APPROVED
            ):
                after = before.model_copy(
                    update={"review_state": ReviewState.PENDING, "updated_at": now}
                )
                self._write_mapping(conn, after)
                _audit(conn, "undo_suggestion", before, after, now, actor)
                return
            raise ValueError("only approved manual or suggestion mappings can be undone")

    def _link_tracked(
        self,
        conn: Connection,
        source: Source,
        competition_id: str,
        now: datetime,
    ) -> bool:
        """Link a curated competition to its shared canonical league.

        Returns True when the registry owns this competition, whether or not a
        mapping was written, so the caller skips name-based auto-mapping.
        """
        entry = self._tracked.resolve(source, competition_id)
        if entry is None:
            return False
        existing = _optional_mapping_row(conn, source, competition_id)
        if existing is not None and MappingMethod(existing["method"]) is MappingMethod.MANUAL:
            return True
        league = self._tracked_league(conn, entry, now)
        if (
            existing is not None
            and existing["league_id"] == league.league_id
            and MappingMethod(existing["method"]) is MappingMethod.TRACKED_REGISTRY
        ):
            return True
        before = _mapping(existing) if existing is not None else None
        changed_at = max(now, before.updated_at) if before is not None else now
        mapping = CompetitionMapping(
            source=source,
            competition_id=competition_id,
            league_id=league.league_id,
            method=MappingMethod.TRACKED_REGISTRY,
            confidence=1.0,
            review_state=ReviewState.APPROVED,
            note=f"tracked:{entry.key}",
            created_at=before.created_at if before is not None else now,
            updated_at=changed_at,
        )
        self._write_mapping(conn, mapping)
        _audit(conn, "link_tracked", before, mapping, changed_at, _SYSTEM_ACTOR)
        return True

    def _tracked_league(
        self,
        conn: Connection,
        entry: TrackedCompetition,
        now: datetime,
    ) -> CanonicalLeague:
        normalized = normalize_taxonomy_text(entry.display_name)
        row = conn.execute(
            "SELECT * FROM canonical_leagues WHERE region_id=? AND normalized_key=? "
            "AND kind=? AND gender IS NULL AND age_group IS NULL AND is_reserve IS NULL "
            "AND active=1;",
            (entry.region_id, normalized, CompetitionKind.UNKNOWN.value),
        ).fetchone()
        if row is not None:
            return _league(row)
        return self._create_league(
            conn,
            entry.region_id,
            CreateLeagueInput(display_name=entry.display_name, kind=CompetitionKind.UNKNOWN),
            now,
        )

    def _auto_map(
        self,
        conn: Connection,
        source: Source,
        evidence: SourceCompetitionEvidence,
        now: datetime,
    ) -> None:
        normalized_name = normalize_taxonomy_text(evidence.competition_name)
        if not normalized_name:
            return
        resolved_regions = {
            region.region_id: region
            for label in (evidence.country_id, evidence.country_name, evidence.country_alias)
            if label and (region := self._countries.resolve(label)) is not None
        }
        if len(resolved_regions) != 1:
            return
        region = next(iter(resolved_regions.values()))
        existing = _optional_mapping_row(conn, source, evidence.competition_id)
        if existing is not None and MappingMethod(existing["method"]) is MappingMethod.MANUAL:
            return
        signature = LeagueSignature(
            normalized_name,
            evidence.kind,
            evidence.gender,
            evidence.age_group,
            evidence.is_reserve,
        )
        rows = conn.execute(
            "SELECT * FROM canonical_leagues WHERE region_id=? AND normalized_key=? "
            "AND kind=? AND gender IS ? AND age_group IS ? AND is_reserve IS ? AND active=1;",
            (
                region.region_id,
                signature.normalized_name,
                signature.kind.value,
                signature.gender,
                signature.age_group,
                _bool_db(signature.is_reserve),
            ),
        ).fetchall()
        if len(rows) > 1:
            return
        method = MappingMethod.VERIFIED_SOURCE_ID
        if rows:
            league = _league(rows[0])
        else:
            alias_rows = conn.execute(
                "SELECT l.* FROM league_aliases a "
                "JOIN canonical_leagues l ON l.league_id=a.league_id "
                "WHERE l.region_id=? AND a.normalized_key=? AND a.kind=? "
                "AND a.gender IS ? AND a.age_group IS ? AND a.is_reserve IS ? "
                "AND l.active=1;",
                (
                    region.region_id,
                    signature.normalized_name,
                    signature.kind.value,
                    signature.gender,
                    signature.age_group,
                    _bool_db(signature.is_reserve),
                ),
            ).fetchall()
            aliases_by_league = {row["league_id"]: row for row in alias_rows}
            if len(aliases_by_league) > 1:
                return
            if aliases_by_league:
                league = _league(next(iter(aliases_by_league.values())))
                method = MappingMethod.EXACT_ALIAS
            else:
                league = self._create_league(
                    conn,
                    region.region_id,
                    CreateLeagueInput(
                        display_name=evidence.competition_name,
                        kind=evidence.kind,
                        gender=evidence.gender,
                        age_group=evidence.age_group,
                        is_reserve=evidence.is_reserve,
                    ),
                    now,
                )
        before = _mapping(existing) if existing is not None else None
        if before is not None and before.league_id == league.league_id:
            return
        changed_at = max(now, before.updated_at) if before is not None else now
        mapping = CompetitionMapping(
            source=source,
            competition_id=evidence.competition_id,
            league_id=league.league_id,
            method=method,
            confidence=1.0,
            review_state=ReviewState.APPROVED,
            note=None,
            created_at=before.created_at if before is not None else changed_at,
            updated_at=changed_at,
        )
        self._write_mapping(conn, mapping)
        _audit(conn, "auto_map", before, mapping, changed_at, _SYSTEM_ACTOR)

    def _create_league(
        self,
        conn: Connection,
        region_id: str,
        value: CreateLeagueInput,
        now: datetime,
    ) -> CanonicalLeague:
        league = CanonicalLeague(
            league_id=f"league:{uuid4()}",
            region_id=region_id,
            display_name=value.display_name,
            normalized_key=normalize_taxonomy_text(value.display_name),
            kind=value.kind,
            gender=value.gender,
            age_group=value.age_group,
            is_reserve=value.is_reserve,
            active=True,
        )
        conn.execute(
            "INSERT INTO canonical_leagues "
            "(league_id, region_id, display_name, normalized_key, kind, gender, age_group, "
            "is_reserve, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?);",
            (
                league.league_id,
                league.region_id,
                league.display_name,
                league.normalized_key,
                league.kind.value,
                league.gender,
                league.age_group,
                _bool_db(league.is_reserve),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO league_aliases "
            "(league_id, normalized_key, kind, gender, age_group, is_reserve) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (
                league.league_id,
                league.normalized_key,
                league.kind.value,
                league.gender,
                league.age_group,
                _bool_db(league.is_reserve),
            ),
        )
        return league

    def _validate_link(self, source_row: Row, league_row: Row) -> None:
        self._validate_prospective_link(
            source_row,
            league_row["region_id"],
            CreateLeagueInput(
                display_name=league_row["display_name"],
                kind=CompetitionKind(league_row["kind"]),
                gender=league_row["gender"],
                age_group=league_row["age_group"],
                is_reserve=(
                    None if league_row["is_reserve"] is None else bool(league_row["is_reserve"])
                ),
            ),
        )

    def _validate_prospective_link(
        self,
        source_row: Row,
        region_id: str,
        league_input: CreateLeagueInput,
    ) -> None:
        resolved_regions = {
            region.region_id
            for field in ("country_id", "country_name", "country_alias")
            if source_row[field]
            and (region := self._countries.resolve(source_row[field])) is not None
        }
        if len(resolved_regions) > 1 or (
            len(resolved_regions) == 1 and region_id not in resolved_regions
        ):
            raise ValueError("country conflict")
        source_kind = CompetitionKind(source_row["kind"])
        if (
            source_kind is not CompetitionKind.UNKNOWN
            and league_input.kind is not CompetitionKind.UNKNOWN
            and source_kind is not league_input.kind
        ):
            raise ValueError("competition kind conflict")
        for label, source_value, target_value in (
            ("gender", source_row["gender"], league_input.gender),
            ("age group", source_row["age_group"], league_input.age_group),
            (
                "reserve state",
                None if source_row["is_reserve"] is None else bool(source_row["is_reserve"]),
                league_input.is_reserve,
            ),
        ):
            if (
                source_value is not None
                and target_value is not None
                and source_value != target_value
            ):
                raise ValueError(f"{label} conflict")

    @staticmethod
    def _write_mapping(conn: Connection, mapping: CompetitionMapping) -> None:
        conn.execute(
            "INSERT INTO competition_mappings "
            "(source, competition_id, league_id, method, confidence, review_state, note, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, competition_id) DO UPDATE SET league_id=excluded.league_id, "
            "method=excluded.method, confidence=excluded.confidence, "
            "review_state=excluded.review_state, note=excluded.note, updated_at=excluded.updated_at;",
            (
                mapping.source.value,
                mapping.competition_id,
                mapping.league_id,
                mapping.method.value,
                mapping.confidence,
                mapping.review_state.value,
                mapping.note,
                mapping.created_at.isoformat(),
                mapping.updated_at.isoformat(),
            ),
        )

    def get_mappings_signature(self) -> str:
        with get_connection(self._db_path) as conn:
            row = conn.execute("SELECT MAX(updated_at) FROM competition_mappings;").fetchone()
            return row[0] if row and row[0] else ""


def _source(value: Source) -> Source:
    try:
        return value if isinstance(value, Source) else Source(value)
    except (TypeError, ValueError) as error:
        raise ValueError("unsupported source") from error


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _bool_db(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _page(limit: int, offset: int, maximum: int = 1000) -> None:
    if isinstance(limit, bool) or limit < 1 or limit > maximum or offset < 0:
        raise ValueError("invalid pagination")


def _expect(actual: str, expected: datetime) -> None:
    if datetime.fromisoformat(actual) != _aware(expected):
        raise TaxonomyConflict("taxonomy record changed")


def _source_row(conn: Connection, source: Source, competition_id: str) -> Row:
    row = cast(
        Row | None,
        conn.execute(
            "SELECT * FROM source_competitions WHERE source=? AND competition_id=?;",
            (source.value, competition_id),
        ).fetchone(),
    )
    if row is None:
        raise LookupError("source competition not found")
    return row


def _mapping_row(conn: Connection, source: Source, competition_id: str) -> Row:
    row = _optional_mapping_row(conn, source, competition_id)
    if row is None:
        raise LookupError("mapping not found")
    return row


def _optional_mapping_row(conn: Connection, source: Source, competition_id: str) -> Row | None:
    return cast(
        Row | None,
        conn.execute(
            "SELECT * FROM competition_mappings WHERE source=? AND competition_id=?;",
            (source.value, competition_id),
        ).fetchone(),
    )


def _league_row(conn: Connection, league_id: str) -> Row:
    row = cast(
        Row | None,
        conn.execute("SELECT * FROM canonical_leagues WHERE league_id=?;", (league_id,)).fetchone(),
    )
    if row is None:
        raise LookupError("league not found")
    return row


def _source_competition(row: Row) -> SourceCompetition:
    return SourceCompetition(
        source=Source(row["source"]),
        provider=row["provider"],
        identity_kind=SourceIdentityKind(row["identity_kind"]),
        competition_id=row["competition_id"],
        competition_name=row["competition_name"],
        country_id=row["country_id"],
        country_name=row["country_name"],
        country_alias=row["country_alias"],
        season=row["season"],
        kind=CompetitionKind(row["kind"]),
        gender=row["gender"],
        age_group=row["age_group"],
        is_reserve=None if row["is_reserve"] is None else bool(row["is_reserve"]),
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
    )


def _mapping(row: Row) -> CompetitionMapping:
    return CompetitionMapping(
        source=Source(row["source"]),
        competition_id=row["competition_id"],
        league_id=row["league_id"],
        method=MappingMethod(row["method"]),
        confidence=row["confidence"],
        review_state=ReviewState(row["review_state"]),
        note=row["note"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _region(row: Row) -> CanonicalRegion:
    columns = set(row.keys())
    return CanonicalRegion(
        region_id=row["region_id"],
        region_class=RegionClass(row["region_class"]),
        iso_code=row["iso_code"],
        display_name=row["region_name"] if "region_name" in columns else row["display_name"],
        normalized_key=row["region_key"] if "region_key" in columns else row["normalized_key"],
        active=bool(row["region_active"] if "region_active" in columns else row["active"]),
    )


def _league(row: Row) -> CanonicalLeague:
    columns = set(row.keys())
    return CanonicalLeague(
        league_id=row["league_id"],
        region_id=row["region_id"],
        display_name=row["league_name"] if "league_name" in columns else row["display_name"],
        normalized_key=row["league_key"] if "league_key" in columns else row["normalized_key"],
        kind=CompetitionKind(row["kind"]),
        gender=row["gender"],
        age_group=row["age_group"],
        is_reserve=None if row["is_reserve"] is None else bool(row["is_reserve"]),
        active=bool(row["league_active"] if "league_active" in columns else row["active"]),
    )


def _audit(
    conn: Connection,
    action: str,
    before: object,
    after: object,
    occurred_at: datetime,
    actor: str,
) -> None:
    def dump(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    conn.execute(
        "INSERT INTO taxonomy_audit "
        "(action, actor, occurred_at, before_json, after_json) VALUES (?, ?, ?, ?, ?);",
        (action, actor, occurred_at.isoformat(), dump(before), dump(after)),
    )

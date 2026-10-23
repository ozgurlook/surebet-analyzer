"""Saved per-user, per-scope taxonomy filters.

A user can save which countries and leagues they want to see on the Events
screen and, independently, on the Opportunities screen. `user_country_filters`
and `user_league_filters` (schema owned by `db.py`) each key on
`(user_id, scope, *_id)`, so one user's saved selection for one scope never
touches another user's rows, or their own rows for the other scope.

A `replace()` call is a full atomic swap of both sets for one `(user_id,
scope)`: every submitted ID is validated against the canonical taxonomy
tables (`TaxonomyRepository.list_regions()` / `get_league()`) *before* any
row is written, so a request containing even one unknown ID leaves the
stored filters completely untouched -- never a partial replacement of
countries with leagues left stale, or vice versa. An empty saved set means
"no narrowing" -- the caller (the Events/Opportunities routes) must treat it
as "show everything" for that scope, not "show nothing".
"""

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .db import get_connection
from .taxonomy import TaxonomyRepository


class FilterScope(StrEnum):
    EVENTS = "events"
    OPPORTUNITIES = "opportunities"


class SavedTaxonomyFilters(BaseModel):
    model_config = ConfigDict(frozen=True)

    country_ids: frozenset[str] = frozenset()
    league_ids: frozenset[str] = frozenset()


class InvalidTaxonomyId(ValueError):
    """A submitted country or league ID is not a known canonical ID."""


class UserFilterRepository:
    def __init__(self, db_path: Path | str, taxonomy_repo: TaxonomyRepository) -> None:
        self._db_path = db_path
        self._taxonomy_repo = taxonomy_repo

    def get(self, user_id: int, scope: FilterScope) -> SavedTaxonomyFilters:
        with get_connection(self._db_path) as conn:
            country_rows = conn.execute(
                "SELECT country_id FROM user_country_filters WHERE user_id=? AND scope=?;",
                (user_id, scope.value),
            ).fetchall()
            league_rows = conn.execute(
                "SELECT league_id FROM user_league_filters WHERE user_id=? AND scope=?;",
                (user_id, scope.value),
            ).fetchall()
        return SavedTaxonomyFilters(
            country_ids=frozenset(row["country_id"] for row in country_rows),
            league_ids=frozenset(row["league_id"] for row in league_rows),
        )

    def replace(
        self,
        user_id: int,
        scope: FilterScope,
        country_ids: Iterable[str],
        league_ids: Iterable[str],
    ) -> SavedTaxonomyFilters:
        """Atomically overwrite both saved sets for `(user_id, scope)`.

        Duplicate IDs are normalized (deduplicated) before storage. Every ID
        is validated against the canonical taxonomy *before* the transaction
        starts, so an invalid ID raises `InvalidTaxonomyId` without writing
        anything -- there is no partial replacement.
        """
        countries = frozenset(country_ids)
        leagues = frozenset(league_ids)
        self._validate_countries(countries)
        self._validate_leagues(leagues)

        with get_connection(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "DELETE FROM user_country_filters WHERE user_id=? AND scope=?;",
                (user_id, scope.value),
            )
            if countries:
                conn.executemany(
                    "INSERT INTO user_country_filters (user_id, scope, country_id) "
                    "VALUES (?, ?, ?);",
                    [(user_id, scope.value, country_id) for country_id in countries],
                )
            conn.execute(
                "DELETE FROM user_league_filters WHERE user_id=? AND scope=?;",
                (user_id, scope.value),
            )
            if leagues:
                conn.executemany(
                    "INSERT INTO user_league_filters (user_id, scope, league_id) VALUES (?, ?, ?);",
                    [(user_id, scope.value, league_id) for league_id in leagues],
                )
        return SavedTaxonomyFilters(country_ids=countries, league_ids=leagues)

    def _validate_countries(self, country_ids: frozenset[str]) -> None:
        if not country_ids:
            return
        known = {region.region_id for region in self._taxonomy_repo.list_regions()}
        unknown = country_ids - known
        if unknown:
            raise InvalidTaxonomyId(f"unknown country id(s): {sorted(unknown)}")

    def _validate_leagues(self, league_ids: frozenset[str]) -> None:
        for league_id in league_ids:
            if self._taxonomy_repo.get_league(league_id) is None:
                raise InvalidTaxonomyId(f"unknown league id: {league_id}")

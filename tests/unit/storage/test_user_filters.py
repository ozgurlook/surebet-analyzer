"""Isolation and atomicity tests for saved per-user taxonomy filters.

`user_country_filters` and `user_league_filters` back one atomic replace per
(user_id, scope): a `replace()` call must overwrite both sets for that scope
in a single transaction, leave the two scopes (`events`/`opportunities`)
fully independent, leave two different users fully independent, normalize
duplicate submitted IDs, and roll back entirely -- both tables, not just one
-- when any submitted ID is not a real canonical ID.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from surebet.storage.auth import AuthRepository
from surebet.storage.db import get_connection, init_db
from surebet.storage.taxonomy import TaxonomyRepository
from surebet.storage.user_filters import (
    FilterScope,
    InvalidTaxonomyId,
    SavedTaxonomyFilters,
    UserFilterRepository,
)
from surebet.taxonomy.models import CompetitionKind
from surebet.taxonomy.normalizer import normalize_taxonomy_text

FAKE_PASSWORD = "correct-horse-battery-staple"
NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _make_two_users(db_path: Path) -> tuple[int, int]:
    auth = AuthRepository(db_path)
    user_a, _ = auth.register_user("alice", FAKE_PASSWORD)
    user_b, _ = auth.register_user("bob", FAKE_PASSWORD)
    return user_a.user_id, user_b.user_id


def _insert_league(db_path: Path, region_id: str, display_name: str) -> str:
    """Create a canonical league directly, standing in for pre-existing data."""
    league_id = f"league:{uuid4()}"
    stamp = NOW.isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO canonical_leagues "
            "(league_id, region_id, display_name, normalized_key, kind, gender, age_group, "
            "is_reserve, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 1, ?, ?);",
            (
                league_id,
                region_id,
                display_name,
                normalize_taxonomy_text(display_name),
                CompetitionKind.UNKNOWN.value,
                stamp,
                stamp,
            ),
        )
    return league_id


@pytest.fixture
def repo(tmp_path: Path) -> UserFilterRepository:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return UserFilterRepository(db_path, TaxonomyRepository(db_path))


def test_filter_scope_has_only_events_and_opportunities() -> None:
    assert {member.value for member in FilterScope} == {"events", "opportunities"}


def test_saved_taxonomy_filters_is_frozen() -> None:
    filters = SavedTaxonomyFilters(country_ids=frozenset({"country:TR"}), league_ids=frozenset())
    with pytest.raises(ValidationError):
        filters.country_ids = frozenset()  # type: ignore[misc]


def test_get_for_a_user_with_no_saved_filters_is_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    filters = repo.get(user_id, FilterScope.EVENTS)

    assert filters.country_ids == frozenset()
    assert filters.league_ids == frozenset()


def test_replace_atomically_stores_both_country_and_league_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    league_id = _insert_league(db_path, "country:TR", "Süper Lig")
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    result = repo.replace(
        user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[league_id]
    )

    assert result.country_ids == frozenset({"country:TR"})
    assert result.league_ids == frozenset({league_id})

    # Reloading confirms the write was actually persisted, not just returned.
    reloaded = repo.get(user_id, FilterScope.EVENTS)
    assert reloaded == result


def test_replace_fully_overwrites_previous_selection_not_accumulates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])
    result = repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:DE"], league_ids=[])

    assert result.country_ids == frozenset({"country:DE"})
    reloaded = repo.get(user_id, FilterScope.EVENTS)
    assert reloaded.country_ids == frozenset({"country:DE"})


def test_empty_replace_means_no_narrowing(tmp_path: Path) -> None:
    """An empty PUT body is a valid way to clear a filter back to 'show everything'."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])
    cleared = repo.replace(user_id, FilterScope.EVENTS, country_ids=[], league_ids=[])

    assert cleared.country_ids == frozenset()
    assert cleared.league_ids == frozenset()
    assert repo.get(user_id, FilterScope.EVENTS).country_ids == frozenset()


def test_scopes_are_fully_independent_for_one_user(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])
    repo.replace(user_id, FilterScope.OPPORTUNITIES, country_ids=["country:DE"], league_ids=[])

    events_filters = repo.get(user_id, FilterScope.EVENTS)
    opportunities_filters = repo.get(user_id, FilterScope.OPPORTUNITIES)

    assert events_filters.country_ids == frozenset({"country:TR"})
    assert opportunities_filters.country_ids == frozenset({"country:DE"})


def test_two_users_filters_for_the_same_scope_are_fully_independent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_a, user_b = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    repo.replace(user_a, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])
    repo.replace(user_b, FilterScope.EVENTS, country_ids=["country:DE"], league_ids=[])

    assert repo.get(user_a, FilterScope.EVENTS).country_ids == frozenset({"country:TR"})
    assert repo.get(user_b, FilterScope.EVENTS).country_ids == frozenset({"country:DE"})


def test_duplicate_ids_in_one_request_are_normalized(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    result = repo.replace(
        user_id,
        FilterScope.EVENTS,
        country_ids=["country:TR", "country:TR", "country:DE"],
        league_ids=[],
    )

    assert result.country_ids == frozenset({"country:TR", "country:DE"})
    assert repo.get(user_id, FilterScope.EVENTS).country_ids == frozenset(
        {"country:TR", "country:DE"}
    )


def test_replace_rejects_unknown_country_id_and_rolls_back_entirely(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    league_id = _insert_league(db_path, "country:TR", "Süper Lig")
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))
    repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])

    with pytest.raises(InvalidTaxonomyId):
        repo.replace(
            user_id,
            FilterScope.EVENTS,
            country_ids=["country:TR", "country:does-not-exist"],
            league_ids=[league_id],
        )

    # Neither the country set nor the (valid) league set changed -- the
    # failure rolled back the whole write, not just the invalid table.
    unchanged = repo.get(user_id, FilterScope.EVENTS)
    assert unchanged.country_ids == frozenset({"country:TR"})
    assert unchanged.league_ids == frozenset()


def test_replace_rejects_unknown_league_id_and_rolls_back_country_ids_too(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))
    repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:TR"], league_ids=[])

    with pytest.raises(InvalidTaxonomyId):
        repo.replace(
            user_id,
            FilterScope.EVENTS,
            country_ids=["country:DE"],
            league_ids=["league:does-not-exist"],
        )

    # The country set must be unchanged at its prior value ("country:TR"),
    # proving the invalid league id rolled back the entire transaction --
    # not just the league table, leaving a partially-applied write.
    unchanged = repo.get(user_id, FilterScope.EVENTS)
    assert unchanged.country_ids == frozenset({"country:TR"})
    assert unchanged.league_ids == frozenset()


def test_replace_with_no_prior_state_and_invalid_id_leaves_zero_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)
    repo = UserFilterRepository(db_path, TaxonomyRepository(db_path))

    with pytest.raises(InvalidTaxonomyId):
        repo.replace(user_id, FilterScope.EVENTS, country_ids=["country:not-real"], league_ids=[])

    with get_connection(db_path) as conn:
        country_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM user_country_filters WHERE user_id=?;", (user_id,)
        ).fetchone()["n"]
        league_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM user_league_filters WHERE user_id=?;", (user_id,)
        ).fetchone()["n"]
    assert country_rows == 0
    assert league_rows == 0

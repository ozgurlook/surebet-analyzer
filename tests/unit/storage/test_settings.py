"""Isolation tests for personal preference vs. shared system settings storage."""

from pathlib import Path

import pytest

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.settings import (
    SystemSettingsRepository,
    SystemSettingsUpdate,
    UserPreferencesRepository,
    UserPreferencesUpdate,
)

FAKE_PASSWORD = "correct-horse-battery-staple"


def _make_two_users(db_path: Path) -> tuple[int, int]:
    auth = AuthRepository(db_path)
    user_a, _ = auth.register_user("alice", FAKE_PASSWORD)
    user_b, _ = auth.register_user("bob", FAKE_PASSWORD)
    return user_a.user_id, user_b.user_id


def test_user_preferences_default_for_a_new_user(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)

    prefs = UserPreferencesRepository(db_path).get(user_id)

    assert prefs.default_budget_cents == 100_000
    assert prefs.include_source_a_match_sides is False
    assert prefs.tracked_competitions_only is True


def test_updating_one_users_preferences_does_not_change_the_others(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_a_id, user_b_id = _make_two_users(db_path)

    repo = UserPreferencesRepository(db_path)
    updated_a = repo.update(
        user_a_id,
        UserPreferencesUpdate(
            default_budget_cents=250_000,
            include_source_a_match_sides=True,
            tracked_competitions_only=False,
        ),
    )

    assert updated_a.default_budget_cents == 250_000
    assert updated_a.include_source_a_match_sides is True
    assert updated_a.tracked_competitions_only is False

    # User B must remain at defaults -- proves the write was scoped to user A alone.
    b_prefs = repo.get(user_b_id)
    assert b_prefs.default_budget_cents == 100_000
    assert b_prefs.include_source_a_match_sides is False
    assert b_prefs.tracked_competitions_only is True

    # Reloading user A confirms the change was actually persisted, not just
    # returned in-memory.
    reloaded_a = repo.get(user_a_id)
    assert reloaded_a == updated_a


def test_partial_update_only_changes_the_supplied_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)

    repo = UserPreferencesRepository(db_path)
    repo.update(user_id, UserPreferencesUpdate(default_budget_cents=500_000))
    updated = repo.update(user_id, UserPreferencesUpdate(include_source_a_match_sides=True))

    assert updated.default_budget_cents == 500_000
    assert updated.include_source_a_match_sides is True
    assert updated.tracked_competitions_only is True


def test_user_preferences_budget_validation_rejects_out_of_range_value(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)

    with pytest.raises(ValueError):
        UserPreferencesUpdate(default_budget_cents=1_000_000_001)

    # Validation must fail while constructing the update model itself, before
    # any SQL runs, so the stored row stays untouched at its default.
    assert UserPreferencesRepository(db_path).get(user_id).default_budget_cents == 100_000


def test_system_settings_default_and_update_is_visible_regardless_of_user(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    _make_two_users(db_path)

    repo = SystemSettingsRepository(db_path)
    settings = repo.get()
    assert settings.refresh_interval_seconds == 30
    assert settings.staleness_limit_seconds == 120

    updated = repo.update(
        SystemSettingsUpdate(refresh_interval_seconds=15, staleness_limit_seconds=90)
    )
    assert updated.refresh_interval_seconds == 15
    assert updated.staleness_limit_seconds == 90

    # A brand new repository instance -- standing in for a request from a
    # completely different user's session -- observes the identical shared
    # value. System settings are a true singleton, never scoped by user_id.
    reloaded = SystemSettingsRepository(db_path).get()
    assert reloaded == updated


def test_system_settings_validation_rejects_out_of_range_value(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with pytest.raises(ValueError):
        SystemSettingsUpdate(refresh_interval_seconds=1_000)

    assert SystemSettingsRepository(db_path).get().refresh_interval_seconds == 30


def test_personal_and_system_updates_are_fully_independent(tmp_path: Path) -> None:
    """There is no single 'update settings' call spanning both sections -- a
    personal-preference write must never touch the system row, and a system
    write must never touch any user's preferences row."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    user_id, _ = _make_two_users(db_path)

    prefs_repo = UserPreferencesRepository(db_path)
    system_repo = SystemSettingsRepository(db_path)

    prefs_repo.update(user_id, UserPreferencesUpdate(default_budget_cents=750_000))
    system_repo.update(SystemSettingsUpdate(staleness_limit_seconds=200))

    assert prefs_repo.get(user_id).default_budget_cents == 750_000
    assert system_repo.get().staleness_limit_seconds == 200
    # Each update must leave the other model's untouched field at its default.
    assert prefs_repo.get(user_id).tracked_competitions_only is True
    assert system_repo.get().refresh_interval_seconds == 30

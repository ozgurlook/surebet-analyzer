"""Personal preference and shared system settings repositories.

These are two intentionally independent repositories, each with its own
model and its own SQL transaction:

- `UserPreferencesRepository` owns `user_preferences`, one row per user
  (`default_budget_cents`, `include_source_a_match_sides`,
  `tracked_competitions_only`). A write only ever touches the calling
  user's own row.
- `SystemSettingsRepository` owns the singleton `system_settings` row
  (`refresh_interval_seconds`, `staleness_limit_seconds`), shared by every
  user with no per-user scoping at all.

There is no combined "update settings" call spanning both -- a failed
personal update can never partially mutate the system row, and vice versa.
"""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .db import get_connection


class UserPreferences(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_budget_cents: int = Field(default=100_000, ge=1_000, le=1_000_000_000)
    include_source_a_match_sides: bool = False
    tracked_competitions_only: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserPreferencesUpdate(BaseModel):
    default_budget_cents: int | None = Field(default=None, ge=1_000, le=1_000_000_000)
    include_source_a_match_sides: bool | None = None
    tracked_competitions_only: bool | None = None


class SystemSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    refresh_interval_seconds: int = Field(default=30, ge=5, le=300)
    staleness_limit_seconds: int = Field(default=75, ge=30, le=600)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SystemSettingsUpdate(BaseModel):
    refresh_interval_seconds: int | None = Field(default=None, ge=5, le=300)
    staleness_limit_seconds: int | None = Field(default=None, ge=30, le=600)


class UserPreferencesRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def get(self, user_id: int) -> UserPreferences:
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT default_budget_cents, include_source_a_match_sides, "
                "tracked_competitions_only, updated_at "
                "FROM user_preferences WHERE user_id = ?;",
                (user_id,),
            ).fetchone()

            if row is None:
                # Registration always inserts a default row atomically, so this
                # only guards against a caller looking up a user_id whose row
                # is somehow missing -- stay defensive rather than raising.
                defaults = UserPreferences()
                conn.execute(
                    "INSERT INTO user_preferences (user_id, default_budget_cents, "
                    "include_source_a_match_sides, tracked_competitions_only, updated_at) "
                    "VALUES (?, ?, ?, ?, ?);",
                    (
                        user_id,
                        defaults.default_budget_cents,
                        int(defaults.include_source_a_match_sides),
                        int(defaults.tracked_competitions_only),
                        defaults.updated_at.isoformat(),
                    ),
                )
                return defaults

            return UserPreferences(
                default_budget_cents=row["default_budget_cents"],
                include_source_a_match_sides=bool(row["include_source_a_match_sides"]),
                tracked_competitions_only=bool(row["tracked_competitions_only"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def update(self, user_id: int, updates: UserPreferencesUpdate) -> UserPreferences:
        current = self.get(user_id)
        new_budget = (
            updates.default_budget_cents
            if updates.default_budget_cents is not None
            else current.default_budget_cents
        )
        new_source_a = (
            updates.include_source_a_match_sides
            if updates.include_source_a_match_sides is not None
            else current.include_source_a_match_sides
        )
        new_tracked_only = (
            updates.tracked_competitions_only
            if updates.tracked_competitions_only is not None
            else current.tracked_competitions_only
        )
        now = datetime.now(UTC)

        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO user_preferences (user_id, default_budget_cents, "
                "include_source_a_match_sides, tracked_competitions_only, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "default_budget_cents = excluded.default_budget_cents, "
                "include_source_a_match_sides = excluded.include_source_a_match_sides, "
                "tracked_competitions_only = excluded.tracked_competitions_only, "
                "updated_at = excluded.updated_at;",
                (
                    user_id,
                    new_budget,
                    int(new_source_a),
                    int(new_tracked_only),
                    now.isoformat(),
                ),
            )

        return UserPreferences(
            default_budget_cents=new_budget,
            include_source_a_match_sides=new_source_a,
            tracked_competitions_only=new_tracked_only,
            updated_at=now,
        )


class SystemSettingsRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def get(self) -> SystemSettings:
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT refresh_interval_seconds, staleness_limit_seconds, updated_at "
                "FROM system_settings WHERE id = 1;"
            ).fetchone()

            if row is None:
                # init_db() seeds this row via INSERT OR IGNORE, so this only
                # guards a database that was somehow created without running
                # the full schema script.
                defaults = SystemSettings()
                conn.execute(
                    "INSERT INTO system_settings (id, refresh_interval_seconds, "
                    "staleness_limit_seconds, updated_at) VALUES (1, ?, ?, ?);",
                    (
                        defaults.refresh_interval_seconds,
                        defaults.staleness_limit_seconds,
                        defaults.updated_at.isoformat(),
                    ),
                )
                return defaults

            return SystemSettings(
                refresh_interval_seconds=row["refresh_interval_seconds"],
                staleness_limit_seconds=row["staleness_limit_seconds"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def update(self, updates: SystemSettingsUpdate) -> SystemSettings:
        current = self.get()
        new_refresh = (
            updates.refresh_interval_seconds
            if updates.refresh_interval_seconds is not None
            else current.refresh_interval_seconds
        )
        new_staleness = (
            updates.staleness_limit_seconds
            if updates.staleness_limit_seconds is not None
            else current.staleness_limit_seconds
        )
        now = datetime.now(UTC)

        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO system_settings (id, refresh_interval_seconds, "
                "staleness_limit_seconds, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "refresh_interval_seconds = excluded.refresh_interval_seconds, "
                "staleness_limit_seconds = excluded.staleness_limit_seconds, "
                "updated_at = excluded.updated_at;",
                (new_refresh, new_staleness, now.isoformat()),
            )

        return SystemSettings(
            refresh_interval_seconds=new_refresh,
            staleness_limit_seconds=new_staleness,
            updated_at=now,
        )

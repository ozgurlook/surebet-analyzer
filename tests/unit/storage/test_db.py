import sqlite3
from pathlib import Path

import pytest

from surebet.storage.db import get_connection, init_db


def test_init_db_creates_schema_and_enables_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        }
        assert "source_snapshots" in tables
        assert "matched_events" in tables
        assert {
            "source_competitions",
            "canonical_regions",
            "canonical_leagues",
            "league_aliases",
            "competition_mappings",
            "taxonomy_audit",
        } <= tables

        region_count = conn.execute("SELECT COUNT(*) FROM canonical_regions;").fetchone()[0]
        assert region_count == 251


def test_init_db_creates_notification_tables_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "notifications.db"
    init_db(db_path)
    init_db(db_path)

    with get_connection(db_path) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")
        }

    assert {
        "telegram_notification_settings",
        "telegram_opportunity_cards",
        "telegram_notification_outbox",
    } <= names
    assert "telegram_opportunity_states" not in names


def test_old_singleton_auth_and_settings_tables_do_not_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }

    assert "auth" not in tables
    assert "settings" not in tables


def test_users_table_has_unique_username_key_and_active_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users);")}
        assert {"user_id", "username_display", "username_key", "password_hash", "active"} <= (
            columns
        )

        conn.execute(
            "INSERT INTO users (username_display, username_key, password_hash, active, "
            "created_at, updated_at) VALUES ('Alice', 'alice', 'hash', 1, 't', 't');"
        )

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (username_display, username_key, password_hash, active, "
            "created_at, updated_at) VALUES ('ALICE', 'alice', 'hash2', 1, 't', 't');"
        )

    with get_connection(db_path) as conn:
        row = conn.execute("SELECT active FROM users WHERE username_key = 'alice';").fetchone()
        assert row["active"] == 1


def test_sessions_require_user_id_and_cascade_on_user_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO users (username_display, username_key, password_hash, active, "
            "created_at, updated_at) VALUES ('Bob', 'bob', 'hash', 1, 't', 't');"
        )
        user_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO sessions (token, user_id, csrf_token, created_at, expires_at) "
            "VALUES ('tok1', ?, 'csrf1', 't', 't');",
            (user_id,),
        )

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions WHERE token = 'tok1';").fetchone()[0]
        assert count == 1

    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?;", (user_id,))

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions WHERE token = 'tok1';").fetchone()[0]
        assert count == 0


def test_sessions_reject_orphan_row_without_matching_user(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sessions (token, user_id, csrf_token, created_at, expires_at) "
            "VALUES ('orphan', 999999, 'csrf', 't', 't');"
        )


def test_user_preferences_table_exists_and_cascades(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(user_preferences);")}
        assert {
            "user_id",
            "default_budget_cents",
            "include_source_a_match_sides",
            "tracked_competitions_only",
            "updated_at",
        } <= columns

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO user_preferences (user_id, updated_at) VALUES (999999, 't');")


def test_system_settings_is_seeded_singleton(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(system_settings);")}
        assert {"refresh_interval_seconds", "staleness_limit_seconds", "updated_at"} <= columns

        row = conn.execute(
            "SELECT refresh_interval_seconds, staleness_limit_seconds FROM system_settings "
            "WHERE id = 1;"
        ).fetchone()
        assert row is not None
        assert row["refresh_interval_seconds"] == 30
        assert row["staleness_limit_seconds"] == 120

        count = conn.execute("SELECT COUNT(*) FROM system_settings;").fetchone()[0]
        assert count == 1


def test_user_country_and_league_filters_enforce_scope_check(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        country_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(user_country_filters);")
        }
        league_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_league_filters);")}
        assert {"user_id", "scope", "country_id"} <= country_columns
        assert {"user_id", "scope", "league_id"} <= league_columns

        cursor = conn.execute(
            "INSERT INTO users (username_display, username_key, password_hash, active, "
            "created_at, updated_at) VALUES ('Cara', 'cara', 'hash', 1, 't', 't');"
        )
        user_id = cursor.lastrowid
        region_id = conn.execute("SELECT region_id FROM canonical_regions LIMIT 1;").fetchone()[0]
        conn.execute(
            "INSERT INTO user_country_filters (user_id, scope, country_id) VALUES (?, ?, ?);",
            (user_id, "events", region_id),
        )

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO user_country_filters (user_id, scope, country_id) VALUES (?, ?, ?);",
            (user_id, "not-a-real-scope", region_id),
        )

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO user_league_filters (user_id, scope, league_id) VALUES (?, ?, ?);",
            (user_id, "bogus", "some-league"),
        )


def test_telegram_tables_include_user_id_ownership_column(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        settings_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")
        }
        card_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_opportunity_cards);")
        }
        outbox_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")
        }

    assert "user_id" in settings_columns
    assert "increase_threshold_bps" in settings_columns
    assert "user_id" in card_columns
    assert "user_id" in outbox_columns


def test_auth_rate_limit_attempts_table_has_expected_columns_and_action_check(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(auth_rate_limit_attempts);")}
        assert columns == {"attempt_id", "client_ip", "action", "attempted_at"}

        conn.execute(
            "INSERT INTO auth_rate_limit_attempts (client_ip, action, attempted_at) "
            "VALUES ('1.2.3.4', 'login', 't');"
        )

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO auth_rate_limit_attempts (client_ip, action, attempted_at) "
            "VALUES ('1.2.3.4', 'not-a-real-action', 't');"
        )


def test_foreign_key_enforcement_rejects_orphan_personal_row(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_connection(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO telegram_notification_settings (user_id, updated_at) VALUES (999999, 't');"
        )


def test_init_db_migrates_old_telegram_notification_settings_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    # Create legacy schema without increase_threshold_bps
    legacy_sql = """
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username_display TEXT NOT NULL,
        username_key TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE telegram_notification_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0,
        chat_id INTEGER,
        minimum_profit_bps INTEGER NOT NULL DEFAULT 95,
        decrease_threshold_bps INTEGER NOT NULL DEFAULT 20,
        alert_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
        validated_chat_id INTEGER,
        validated_at TEXT,
        last_successful_delivery_at TEXT,
        last_error TEXT,
        updated_at TEXT NOT NULL
    );
    INSERT INTO users (username_display, username_key, password_hash, created_at, updated_at)
    VALUES ('Alice', 'alice', 'hash', 'now', 'now');
    INSERT INTO telegram_notification_settings (user_id, updated_at) VALUES (1, 'now');
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(legacy_sql)

    # Calling init_db must run migration without error
    init_db(db_path)

    with get_connection(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")
        }
        assert "increase_threshold_bps" in columns
        row = conn.execute(
            "SELECT increase_threshold_bps FROM telegram_notification_settings WHERE user_id = 1;"
        ).fetchone()
        assert row["increase_threshold_bps"] == 20


def test_system_settings_migration_updates_legacy_staleness_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_settings.db"
    init_db(db_path)
    # Simulate legacy database that had staleness_limit_seconds = 75
    with get_connection(db_path) as conn:
        conn.execute("UPDATE system_settings SET staleness_limit_seconds = 75 WHERE id = 1;")

    # Calling init_db again must migrate staleness_limit_seconds to 120
    init_db(db_path)

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT staleness_limit_seconds FROM system_settings WHERE id = 1;"
        ).fetchone()
        assert row["staleness_limit_seconds"] == 120


def test_get_connection_configures_synchronous_and_cache_size(tmp_path: Path) -> None:
    db_path = tmp_path / "pragma.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
        cache_size = conn.execute("PRAGMA cache_size;").fetchone()[0]

        assert sync_mode == 1  # 1 = NORMAL (default was 2 = FULL)
        assert cache_size == -32000  # 32 MB cache


def test_accounting_schema_migration_on_legacy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_acc.db"
    legacy_sql = """
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username_display TEXT NOT NULL,
        username_key TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE accounting_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        tx_type TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        tx_date TEXT NOT NULL,
        note TEXT,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        created_at TEXT NOT NULL,
        cancelled_at TEXT
    );
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(legacy_sql)

    # Calling init_db must migrate accounting_transactions schema and create indices without error
    init_db(db_path)

    with get_connection(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(accounting_transactions);")}
        assert "account_id" in cols
        assert "user_id" in cols
        indices = {row[1] for row in conn.execute("PRAGMA index_list(accounting_transactions);")}
        assert "idx_acc_account_date" in indices
        assert "idx_acc_user_date" in indices

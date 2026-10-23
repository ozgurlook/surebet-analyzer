import sqlite3
from pathlib import Path

from surebet.storage.db import init_db


def _legacy_settings_db(path: Path) -> None:
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "ALTER TABLE telegram_notification_settings "
            "RENAME COLUMN alert_cooldown_seconds TO decrease_cooldown_seconds;"
        )


def test_migration_renames_the_cooldown_column(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    _legacy_settings_db(path)

    init_db(path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")
        }
    assert "alert_cooldown_seconds" in columns
    assert "decrease_cooldown_seconds" not in columns


def test_migration_preserves_the_configured_value(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    _legacy_settings_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO users "
            "(user_id, username_display, username_key, password_hash, created_at, updated_at) "
            "VALUES (1, 'u', 'u', 'h', '2026-09-01', '2026-09-01');"
        )
        conn.execute(
            "INSERT INTO telegram_notification_settings "
            "(user_id, enabled, minimum_profit_bps, decrease_threshold_bps, "
            "decrease_cooldown_seconds, increase_threshold_bps, updated_at) "
            "VALUES (1, 1, 95, 20, 900, 20, '2026-09-01');"
        )

    init_db(path)

    with sqlite3.connect(path) as conn:
        value = conn.execute(
            "SELECT alert_cooldown_seconds FROM telegram_notification_settings WHERE user_id = 1;"
        ).fetchone()[0]
    assert value == 900


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    _legacy_settings_db(path)

    init_db(path)
    init_db(path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")
        }
    assert "alert_cooldown_seconds" in columns


def test_migration_adds_notify_market_columns(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    init_db(path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")
        }
    assert "notify_1x2" in columns
    assert "notify_ou25" in columns

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from surebet.notifications.models import OutboxActionType
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository

SUNSET_TEXT = "ℹ️ Bu kart yeni bildirim sistemine geçiş nedeniyle kapatıldı."

_LEGACY_OUTBOX_SQL = """
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username_display TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE telegram_opportunity_states (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    matched_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    status TEXT NOT NULL,
    message_id INTEGER,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, matched_id, generation)
);

CREATE TABLE telegram_notification_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    matched_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    action TEXT NOT NULL,
    coalesce_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (coalesce_key)
);
"""


def _create_legacy_outbox_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_LEGACY_OUTBOX_SQL)
        conn.execute(
            "INSERT INTO users "
            "(user_id, username_display, username_key, password_hash, created_at, updated_at) "
            "VALUES (1, 'u', 'u', 'h', '2026-09-01', '2026-09-01');"
        )
        conn.executemany(
            "INSERT INTO telegram_opportunity_states VALUES "
            "(?, ?, ?, ?, ?, ?, '{}', '2026-09-01', '2026-09-01');",
            [
                # Live cards that were actually posted: each must be sunset with
                # its OWN message_id so the edit lands on the right message.
                (1, 99, "event-1", 1, "active", 4242),
                (1, 99, "event-2", 1, "degraded", 5150),
                # Never posted -- there is no message to edit.
                (1, 99, "event-3", 1, "active", None),
                # Already finished: the user has seen its closing message.
                (1, 99, "event-4", 1, "lost", 7000),
                (1, 99, "event-5", 1, "closed", 7001),
            ],
        )
        conn.executemany(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, matched_id, generation, action, coalesce_key, payload_json, "
            "status, attempt_count, next_attempt_at, created_at, updated_at) "
            "VALUES (1, 99, ?, 1, 'send_full', ?, '{}', ?, 0, "
            "'2026-09-01', '2026-09-01', '2026-09-01');",
            [
                ("event-1", "full:1:99:event-1:1", "pending"),
                ("event-9", None, "delivered"),
            ],
        )


def test_schema_creates_opportunity_cards_and_no_legacy_states(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    init_db(path)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
        assert "telegram_opportunity_cards" in tables
        assert "telegram_opportunity_states" not in tables

        # Verify telegram_opportunity_cards columns
        card_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(telegram_opportunity_cards);")
        }
        expected_card_columns = {
            "user_id",
            "chat_id",
            "card_key",
            "matched_id",
            "market_type",
            "generation",
            "status",
            "message_id",
            "state_json",
            "created_at",
            "updated_at",
        }
        assert card_columns == expected_card_columns

        # Verify primary key of telegram_opportunity_cards: (user_id, card_key, generation)
        pk_info = {
            row["name"]: row["pk"]
            for row in conn.execute("PRAGMA table_info(telegram_opportunity_cards);")
            if row["pk"] > 0
        }
        assert set(pk_info.keys()) == {"user_id", "card_key", "generation"}

        # Verify index idx_telegram_cards_user_status exists
        indices = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index';")
        }
        assert "idx_telegram_cards_user_status" in indices


def test_schema_includes_outbox_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "surebet.db"
    init_db(path)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")
        }
        assert {"card_key", "message_id", "silent", "priority"}.issubset(columns)


def test_migrate_telegram_outbox_adds_columns_to_legacy_db(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _create_legacy_outbox_db(path)

    # Before migration: outbox does not have new columns
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        initial_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")
        }
        assert "card_key" not in initial_columns
        assert "message_id" not in initial_columns
        assert "silent" not in initial_columns
        assert "priority" not in initial_columns

    # Run init_db which triggers _migrate_telegram_outbox
    init_db(path)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        migrated_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")
        }
        assert {"card_key", "message_id", "silent", "priority"}.issubset(migrated_columns)

        # Legacy row is intact with defaults
        row = conn.execute(
            "SELECT action, status, matched_id FROM telegram_notification_outbox "
            "WHERE action = 'send_full';"
        ).fetchone()
        # The pending legacy row is gone (transient, old coalesce_key format);
        # the delivered one is history and stays.
        assert row is not None
        assert row["action"] == "send_full"
        assert row["status"] == "delivered"
        assert row["matched_id"] == "event-9"


def _sunset_rows(path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return list(
            conn.execute(
                "SELECT * FROM telegram_notification_outbox "
                "WHERE action = 'edit_card' ORDER BY matched_id;"
            )
        )


def test_sunset_edits_every_live_legacy_card_with_its_own_message_id(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _create_legacy_outbox_db(path)

    init_db(path)

    rows = _sunset_rows(path)
    assert [row["matched_id"] for row in rows] == ["event-1", "event-2"]
    assert [row["message_id"] for row in rows] == [4242, 5150]
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert payload["text"] == SUNSET_TEXT
        assert payload["action"] == "edit_card"
        # Ruling R3: the payload's message_id is per row and agrees with the
        # column, so the sunset edits the stale card instead of sending anew.
        assert payload["message_id"] == row["message_id"]
        assert row["status"] == "pending"
        assert row["card_key"] is None


def test_sunset_rows_are_deliverable_as_edits_after_the_table_is_gone(tmp_path: Path) -> None:
    """The sunset edit must survive the DROP: it carries its own message_id, so
    the dispatcher never needs the legacy state row it was derived from."""
    path = tmp_path / "legacy.db"
    _create_legacy_outbox_db(path)

    init_db(path)

    due = NotificationRepository(path).list_due_actions(datetime.now(UTC))
    edits = [record for record in due if record.action is OutboxActionType.EDIT_CARD]
    assert sorted(record.message_id for record in edits) == [4242, 5150]
    for record in edits:
        assert record.text == SUNSET_TEXT
        assert record.card_key == ""  # nothing to look up; the message_id is enough


def test_sunset_drops_the_legacy_states_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _create_legacy_outbox_db(path)

    init_db(path)

    with sqlite3.connect(path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
    assert "telegram_opportunity_states" not in tables


def test_sunset_leaves_a_fresh_database_untouched(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    init_db(path)

    assert _sunset_rows(path) == []


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _create_legacy_outbox_db(path)

    init_db(path)
    first = [dict(row) for row in _sunset_rows(path)]
    init_db(path)
    second = [dict(row) for row in _sunset_rows(path)]

    assert first == second
    assert len(second) == 2

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")
        }
        assert {"card_key", "message_id", "silent", "priority"}.issubset(columns)

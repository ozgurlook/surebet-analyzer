"""Database initialization and connection management."""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from surebet.taxonomy.normalizer import CountryDictionary

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username_display TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    default_budget_cents INTEGER NOT NULL DEFAULT 100000,
    include_source_a_match_sides INTEGER NOT NULL DEFAULT 0,
    tracked_competitions_only INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    refresh_interval_seconds INTEGER NOT NULL DEFAULT 30,
    staleness_limit_seconds INTEGER NOT NULL DEFAULT 120,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO system_settings (id, refresh_interval_seconds, staleness_limit_seconds, updated_at)
VALUES (1, 30, 120, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

CREATE TABLE IF NOT EXISTS auth_rate_limit_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_ip TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('login', 'register')),
    attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_rate_limit_lookup
ON auth_rate_limit_attempts(client_ip, action, attempted_at);

CREATE TABLE IF NOT EXISTS source_snapshots (
    source TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    last_success_at TEXT,
    last_batch_started_at TEXT,
    last_batch_finished_at TEXT,
    events_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matched_events (
    id TEXT PRIMARY KEY,
    competition TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    quotes_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_regions (
    region_id TEXT PRIMARY KEY,
    region_class TEXT NOT NULL,
    iso_code TEXT,
    display_name TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_leagues (
    league_id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES canonical_regions(region_id),
    display_name TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    gender TEXT,
    age_group TEXT,
    is_reserve INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_competitions (
    source TEXT NOT NULL,
    competition_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    identity_kind TEXT NOT NULL,
    competition_name TEXT NOT NULL,
    country_id TEXT,
    country_name TEXT,
    country_alias TEXT,
    season TEXT,
    kind TEXT NOT NULL,
    gender TEXT,
    age_group TEXT,
    is_reserve INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, competition_id)
);

CREATE TABLE IF NOT EXISTS league_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id TEXT NOT NULL REFERENCES canonical_leagues(league_id) ON DELETE CASCADE,
    normalized_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    gender TEXT,
    age_group TEXT,
    is_reserve INTEGER,
    UNIQUE (league_id, normalized_key, kind, gender, age_group, is_reserve)
);

CREATE TABLE IF NOT EXISTS competition_mappings (
    source TEXT NOT NULL,
    competition_id TEXT NOT NULL,
    league_id TEXT NOT NULL REFERENCES canonical_leagues(league_id),
    method TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    review_state TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, competition_id),
    FOREIGN KEY (source, competition_id)
        REFERENCES source_competitions(source, competition_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS taxonomy_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT
);

CREATE TABLE IF NOT EXISTS user_country_filters (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('events', 'opportunities')),
    country_id TEXT NOT NULL REFERENCES canonical_regions(region_id),
    PRIMARY KEY (user_id, scope, country_id)
);

CREATE TABLE IF NOT EXISTS user_league_filters (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('events', 'opportunities')),
    league_id TEXT NOT NULL REFERENCES canonical_leagues(league_id),
    PRIMARY KEY (user_id, scope, league_id)
);

CREATE TABLE IF NOT EXISTS telegram_notification_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    chat_id INTEGER,
    minimum_profit_bps INTEGER NOT NULL DEFAULT 95,
    decrease_threshold_bps INTEGER NOT NULL DEFAULT 20,
    alert_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    increase_threshold_bps INTEGER NOT NULL DEFAULT 20,
    validated_chat_id INTEGER,
    validated_at TEXT,
    last_successful_delivery_at TEXT,
    last_error TEXT,
    notify_1x2 INTEGER NOT NULL DEFAULT 1,
    notify_ou25 INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    CHECK (chat_id IS NULL OR chat_id > 0),
    CHECK (minimum_profit_bps BETWEEN 1 AND 10000),
    CHECK (decrease_threshold_bps BETWEEN 1 AND 10000),
    CHECK (alert_cooldown_seconds BETWEEN 60 AND 86400),
    CHECK (increase_threshold_bps BETWEEN 1 AND 10000),
    CHECK (notify_1x2 IN (0, 1)),
    CHECK (notify_ou25 IN (0, 1))
);


CREATE TABLE IF NOT EXISTS telegram_opportunity_cards (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    card_key TEXT NOT NULL,
    matched_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    generation INTEGER NOT NULL,
    status TEXT NOT NULL,
    message_id INTEGER,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, card_key, generation)
);

CREATE TABLE IF NOT EXISTS telegram_notification_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    card_key TEXT,
    matched_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    action TEXT NOT NULL,
    coalesce_key TEXT,
    message_id INTEGER,
    silent INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 2,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (coalesce_key)
);

CREATE TABLE IF NOT EXISTS telegram_muted_cards (
    chat_id INTEGER NOT NULL,
    card_key TEXT NOT NULL,
    muted_until TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, card_key)
);

CREATE TABLE IF NOT EXISTS telegram_favorite_cards (
    chat_id INTEGER NOT NULL,
    card_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, card_key)
);

CREATE TABLE IF NOT EXISTS bankroll_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    account_name TEXT NOT NULL,
    initial_balance_cents INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, account_name)
);

CREATE TABLE IF NOT EXISTS accounting_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    tx_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    tx_date TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    account_id INTEGER REFERENCES bankroll_accounts(id) ON DELETE SET NULL,
    user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_matched_events_kickoff ON matched_events(kickoff);
CREATE INDEX IF NOT EXISTS idx_mapping_review_state ON competition_mappings(review_state);
CREATE INDEX IF NOT EXISTS idx_leagues_normalized_key ON canonical_leagues(normalized_key);
CREATE INDEX IF NOT EXISTS idx_leagues_region_id ON canonical_leagues(region_id);
CREATE INDEX IF NOT EXISTS idx_source_competitions_last_seen ON source_competitions(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_telegram_cards_user_status
ON telegram_opportunity_cards(user_id, status);
CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due
ON telegram_notification_outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_acc_chat_date ON accounting_transactions (chat_id, tx_date, status);
CREATE INDEX IF NOT EXISTS idx_acc_chat_created ON accounting_transactions (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bankroll_accounts_user ON bankroll_accounts(user_id, is_active);
"""


def init_db(db_path: Path | str) -> None:
    """Initialize SQLite database with WAL mode and schema."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_telegram_settings(conn)
        _migrate_telegram_outbox(conn)
        _migrate_telegram_cards(conn)
        _migrate_system_settings(conn)
        _migrate_accounting_schema(conn)
        _seed_regions(conn)


def _migrate_accounting_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(accounting_transactions);")}
    if not columns:
        return
    if "account_id" not in columns:
        conn.execute(
            "ALTER TABLE accounting_transactions ADD COLUMN account_id INTEGER REFERENCES bankroll_accounts(id) ON DELETE SET NULL;"
        )
    if "user_id" not in columns:
        conn.execute(
            "ALTER TABLE accounting_transactions ADD COLUMN user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE;"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_acc_account_date ON accounting_transactions (account_id, tx_date, status);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_acc_user_date ON accounting_transactions (user_id, tx_date, status);"
    )


def _migrate_system_settings(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT staleness_limit_seconds FROM system_settings WHERE id = 1;"
    ).fetchone()
    if row and row[0] < 120:
        conn.execute(
            "UPDATE system_settings SET staleness_limit_seconds = 120, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now') WHERE id = 1;"
        )


def _migrate_telegram_settings(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_settings);")}
    if columns and "increase_threshold_bps" not in columns:
        conn.execute(
            "ALTER TABLE telegram_notification_settings "
            "ADD COLUMN increase_threshold_bps INTEGER NOT NULL DEFAULT 20;"
        )
    if "decrease_cooldown_seconds" in columns and "alert_cooldown_seconds" not in columns:
        # One anchor means one cooldown; the old name promised a decrease-only knob.
        conn.execute(
            "ALTER TABLE telegram_notification_settings "
            "RENAME COLUMN decrease_cooldown_seconds TO alert_cooldown_seconds;"
        )
    if columns and "notify_1x2" not in columns:
        conn.execute(
            "ALTER TABLE telegram_notification_settings "
            "ADD COLUMN notify_1x2 INTEGER NOT NULL DEFAULT 1;"
        )
    if columns and "notify_ou25" not in columns:
        conn.execute(
            "ALTER TABLE telegram_notification_settings "
            "ADD COLUMN notify_ou25 INTEGER NOT NULL DEFAULT 1;"
        )


def _migrate_telegram_outbox(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(telegram_notification_outbox);")}
    if not columns:
        return
    for name, ddl in (
        ("card_key", "ADD COLUMN card_key TEXT"),
        ("message_id", "ADD COLUMN message_id INTEGER"),
        ("silent", "ADD COLUMN silent INTEGER NOT NULL DEFAULT 0"),
        ("priority", "ADD COLUMN priority INTEGER NOT NULL DEFAULT 2"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE telegram_notification_outbox {ddl};")


CARD_SUNSET_TEXT = "ℹ️ Bu kart yeni bildirim sistemine geçiş nedeniyle kapatıldı."


def _migrate_telegram_cards(conn: sqlite3.Connection) -> None:
    """Sunset the pre-card notification path (design spec 8.2).

    Dropping `telegram_opportunity_states` silently would leave a frozen
    "🟢 Aktif" card in every user's chat forever, so each still-open legacy
    card is first edited into a closing note. The edit row carries THAT row's
    own `message_id` in both its column and its payload, so the dispatcher can
    deliver it after the table is gone -- it never has to look a state up.

    Idempotent by construction: once the legacy table is dropped there is
    nothing left to find and a second run returns immediately.
    """
    cards_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'telegram_opportunity_cards';"
    ).fetchone()
    if cards_exists:
        conn.execute("DELETE FROM telegram_opportunity_cards WHERE market_type = 'BTTS';")
        conn.execute(
            "DELETE FROM telegram_notification_outbox "
            "WHERE card_key LIKE '%:BTTS' OR coalesce_key LIKE '%:BTTS:%';"
        )

    legacy_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'telegram_opportunity_states';"
    ).fetchone()
    if legacy_exists is None:
        return

    # Pending rows are transient and carry the old coalesce_key format.
    conn.execute("DELETE FROM telegram_notification_outbox WHERE status = 'pending';")

    now_iso = datetime.now(UTC).isoformat()
    stale = conn.execute(
        "SELECT user_id, chat_id, matched_id, generation, message_id "
        "FROM telegram_opportunity_states "
        "WHERE message_id IS NOT NULL AND status NOT IN ('closed', 'lost') "
        "ORDER BY user_id, matched_id, generation;"
    ).fetchall()
    for user_id, chat_id, matched_id, generation, message_id in stale:
        payload = json.dumps(
            {
                "action": "edit_card",
                "text": CARD_SUNSET_TEXT,
                "fingerprint": f"sunset:{message_id}",
                "next_attempt_at": now_iso,
                "message_id": message_id,
                "silent": True,
            },
            ensure_ascii=False,
        )
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, coalesce_key, "
            "message_id, silent, priority, payload_json, status, attempt_count, "
            "next_attempt_at, last_error, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, 'edit_card', ?, ?, 1, 2, ?, 'pending', 0, "
            "?, NULL, ?, ?);",
            (
                user_id,
                chat_id,
                matched_id,
                generation,
                f"sunset:{user_id}:{chat_id}:{matched_id}:{generation}",
                message_id,
                payload,
                now_iso,
                now_iso,
                now_iso,
            ),
        )

    conn.execute("DROP TABLE telegram_opportunity_states;")


def _seed_regions(conn: sqlite3.Connection) -> None:
    dictionary = CountryDictionary.load_packaged()
    timestamp = datetime.now(UTC).isoformat()
    for region in (*dictionary.special_regions, *dictionary.countries):
        conn.execute(
            "INSERT OR IGNORE INTO canonical_regions "
            "(region_id, region_class, iso_code, display_name, normalized_key, active, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            (
                region.region_id,
                region.region_class.value,
                region.iso_code,
                region.display_name,
                region.normalized_key,
                int(region.active),
                timestamp,
                timestamp,
            ),
        )


@contextmanager
def get_connection(db_path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    """Provide a connection configured with WAL and row factory."""
    path = Path(db_path)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -32000;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

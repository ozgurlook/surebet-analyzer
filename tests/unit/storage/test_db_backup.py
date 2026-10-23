from pathlib import Path

import pytest

from surebet.storage.backup import backup_database
from surebet.storage.db import get_connection, init_db


def test_backup_includes_committed_wal_data_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    destination = tmp_path / "transfer.db"
    init_db(source)
    with get_connection(source) as conn:
        conn.execute(
            "INSERT INTO users (username_display, username_key, password_hash, created_at, updated_at) "
            "VALUES ('Owner', 'owner', 'hash', '2026-01-01', '2026-01-01')"
        )

    backup_database(source, destination)

    with get_connection(destination) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT username_key FROM users").fetchone()[0] == "owner"
    with get_connection(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_backup_refuses_to_replace_an_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    destination = tmp_path / "transfer.db"
    init_db(source)
    destination.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError):
        backup_database(source, destination)

    assert destination.read_text(encoding="utf-8") == "preserve"

"""Create a consistent SQLite snapshot for moving an installation."""

import argparse
from pathlib import Path

from surebet.storage.db import get_connection


def backup_database(source: Path, destination: Path) -> None:
    """Copy committed database state, including pages still in the WAL file."""
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with get_connection(source) as source_conn, get_connection(destination) as dest_conn:
            source_conn.backup(dest_conn)
            result = dest_conn.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
    except Exception:
        destination.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite transfer backup")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backup_database(args.source, args.output)
    print(f"Backup created: {args.output}")


if __name__ == "__main__":
    main()

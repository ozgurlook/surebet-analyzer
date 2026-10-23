"""Snapshot repository for sportsbook batch data and source health."""

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from surebet.domain.enums import Source
from surebet.sources.models import CollectionBatch

from .db import get_connection


class SourceHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SourceHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Source
    status: SourceHealthStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    event_count: int
    error_message: str | None = None
    last_success_at: datetime | None = None
    updated_at: datetime


class SnapshotRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def save_batch(self, batch: CollectionBatch, duration_ms: int = 0) -> None:
        now = datetime.now(UTC)
        events_json = json.dumps([event.model_dump(mode="json") for event in batch.events])
        source_name = batch.source.value

        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO source_snapshots (source, started_at, finished_at, status, "
                "duration_ms, event_count, error_message, last_success_at, "
                "last_batch_started_at, last_batch_finished_at, events_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET "
                "started_at = excluded.started_at, "
                "finished_at = excluded.finished_at, "
                "status = excluded.status, "
                "duration_ms = excluded.duration_ms, "
                "event_count = excluded.event_count, "
                "error_message = NULL, "
                "last_success_at = excluded.finished_at, "
                "last_batch_started_at = excluded.last_batch_started_at, "
                "last_batch_finished_at = excluded.last_batch_finished_at, "
                "events_json = excluded.events_json, "
                "updated_at = excluded.updated_at;",
                (
                    source_name,
                    batch.started_at.isoformat(),
                    batch.finished_at.isoformat(),
                    SourceHealthStatus.HEALTHY.value,
                    duration_ms,
                    len(batch.events),
                    None,
                    batch.finished_at.isoformat(),
                    batch.started_at.isoformat(),
                    batch.finished_at.isoformat(),
                    events_json,
                    now.isoformat(),
                ),
            )

    def record_failure(
        self,
        source: Source,
        started_at: datetime,
        error_message: str,
        duration_ms: int = 0,
        finished_at: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        finished_time = finished_at if finished_at is not None else now
        source_name = source.value

        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT last_success_at, last_batch_started_at, last_batch_finished_at, "
                "events_json, event_count FROM source_snapshots WHERE source = ?;",
                (source_name,),
            ).fetchone()

            last_success = row["last_success_at"] if row else None
            last_batch_start = row["last_batch_started_at"] if row else None
            last_batch_end = row["last_batch_finished_at"] if row else None
            events_json = row["events_json"] if row else None
            event_count = row["event_count"] if row else 0

            status = (
                SourceHealthStatus.DEGRADED.value
                if last_success is not None
                else SourceHealthStatus.UNAVAILABLE.value
            )

            conn.execute(
                "INSERT INTO source_snapshots (source, started_at, finished_at, status, "
                "duration_ms, event_count, error_message, last_success_at, "
                "last_batch_started_at, last_batch_finished_at, events_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET "
                "started_at = excluded.started_at, "
                "finished_at = excluded.finished_at, "
                "status = excluded.status, "
                "duration_ms = excluded.duration_ms, "
                "error_message = excluded.error_message, "
                "updated_at = excluded.updated_at;",
                (
                    source_name,
                    started_at.isoformat(),
                    finished_time.isoformat(),
                    status,
                    duration_ms,
                    event_count,
                    error_message,
                    last_success,
                    last_batch_start,
                    last_batch_end,
                    events_json,
                    now.isoformat(),
                ),
            )

    def get_latest_batch(self, source: Source) -> CollectionBatch | None:
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT last_batch_started_at, last_batch_finished_at, events_json "
                "FROM source_snapshots WHERE source = ?;",
                (source.value,),
            ).fetchone()

            if row is None or not row["events_json"] or not row["last_batch_started_at"]:
                return None

            raw_events: list[dict[str, Any]] = json.loads(row["events_json"])
            started_at = datetime.fromisoformat(row["last_batch_started_at"])
            finished_at = datetime.fromisoformat(row["last_batch_finished_at"])

            return CollectionBatch.model_validate(
                {
                    "source": source.value,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "events": raw_events,
                }
            )

    def purge_expired(
        self,
        now: datetime,
        grace: timedelta = timedelta(minutes=15),
    ) -> dict[Source, int]:
        """Remove events whose kickoff plus grace is not later than now.

        Only rewrites events_json/event_count/updated_at on affected source_snapshots
        rows; snapshot rows themselves and all taxonomy tables are never touched.
        """
        removed: dict[Source, int] = {}
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT source, events_json FROM source_snapshots WHERE events_json IS NOT NULL;"
            ).fetchall()

            for row in rows:
                try:
                    src = Source(row["source"])
                except ValueError:
                    continue

                raw_events: list[dict[str, Any]] = json.loads(row["events_json"])
                survivors = [
                    raw_event
                    for raw_event in raw_events
                    if datetime.fromisoformat(raw_event["kickoff"]) + grace > now
                ]

                purged_count = len(raw_events) - len(survivors)
                if purged_count == 0:
                    continue

                removed[src] = purged_count
                conn.execute(
                    "UPDATE source_snapshots SET events_json = ?, event_count = ?, "
                    "updated_at = ? WHERE source = ?;",
                    (json.dumps(survivors), len(survivors), now.isoformat(), src.value),
                )

        return removed

    def get_source_health(self) -> dict[Source, SourceHealth]:
        result: dict[Source, SourceHealth] = {}
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT source, started_at, finished_at, status, duration_ms, event_count, "
                "error_message, last_success_at, updated_at FROM source_snapshots;"
            ).fetchall()

            for row in rows:
                try:
                    src = Source(row["source"])
                except ValueError:
                    continue
                result[src] = SourceHealth(
                    source=src,
                    status=SourceHealthStatus(row["status"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    finished_at=datetime.fromisoformat(row["finished_at"]),
                    duration_ms=row["duration_ms"],
                    event_count=row["event_count"],
                    error_message=row["error_message"],
                    last_success_at=(
                        datetime.fromisoformat(row["last_success_at"])
                        if row["last_success_at"]
                        else None
                    ),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )

        return result

    def get_snapshots_signature(self) -> tuple[tuple[str, str, str, int], ...]:
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT source, updated_at, status, event_count FROM source_snapshots ORDER BY source;"
            ).fetchall()
            return tuple(
                (r["source"], r["updated_at"] or "", r["status"], r["event_count"]) for r in rows
            )

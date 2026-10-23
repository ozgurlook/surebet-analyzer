"""Durable per-IP authentication rate limiting.

`AuthRateLimitRepository` tracks failed login and registration attempts per
client IP address in SQLite, one row per failed attempt, so the rate limit
window is a genuinely rolling 15-minute window rather than a fixed bucket.

Only the client IP string, the action, and the attempt timestamp are ever
stored -- never usernames, passwords, registration codes, session tokens, or
forwarded-header contents. This module has no knowledge of HTTP; the route
layer is responsible for extracting a trustworthy client IP (e.g.
`request.client.host`, never an unauthenticated `X-Forwarded-For` value) and
passing it in as `client_ip`.
"""

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .db import get_connection

MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)


class AuthAction(StrEnum):
    LOGIN = "login"
    REGISTER = "register"


class RateLimitDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    retry_after_seconds: int


class AuthRateLimitRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def check(
        self, client_ip: str, action: AuthAction, now: datetime | None = None
    ) -> RateLimitDecision:
        """Return whether `client_ip` may attempt `action` right now, without
        recording anything."""
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            attempted_at_values = self._attempts_in_window(conn, client_ip, action, moment)
        return self._decide(attempted_at_values, moment)

    def record_failure(
        self, client_ip: str, action: AuthAction, now: datetime | None = None
    ) -> RateLimitDecision:
        """Record a failed `action` attempt from `client_ip` and return the
        resulting decision for the *next* attempt."""
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO auth_rate_limit_attempts (client_ip, action, attempted_at) "
                "VALUES (?, ?, ?);",
                (client_ip, action.value, moment.isoformat()),
            )
            attempted_at_values = self._attempts_in_window(conn, client_ip, action, moment)
        return self._decide(attempted_at_values, moment)

    def clear(self, client_ip: str, action: AuthAction) -> None:
        """Clear the failed-attempt bucket for this `client_ip` and `action`
        only (e.g. after a successful login or registration)."""
        with get_connection(self._db_path) as conn:
            conn.execute(
                "DELETE FROM auth_rate_limit_attempts WHERE client_ip = ? AND action = ?;",
                (client_ip, action.value),
            )

    def cleanup_expired(self, now: datetime | None = None) -> None:
        """Delete attempt rows older than the rolling window, across all
        clients and actions, so the table does not grow unbounded."""
        moment = now or datetime.now(UTC)
        cutoff = (moment - WINDOW).isoformat()
        with get_connection(self._db_path) as conn:
            conn.execute(
                "DELETE FROM auth_rate_limit_attempts WHERE attempted_at <= ?;",
                (cutoff,),
            )

    @staticmethod
    def _attempts_in_window(
        conn: sqlite3.Connection, client_ip: str, action: AuthAction, moment: datetime
    ) -> list[str]:
        cutoff = (moment - WINDOW).isoformat()
        rows = conn.execute(
            "SELECT attempted_at FROM auth_rate_limit_attempts "
            "WHERE client_ip = ? AND action = ? AND attempted_at > ? "
            "ORDER BY attempted_at ASC;",
            (client_ip, action.value, cutoff),
        ).fetchall()
        return [row["attempted_at"] for row in rows]

    @staticmethod
    def _decide(attempted_at_values: list[str], moment: datetime) -> RateLimitDecision:
        if len(attempted_at_values) < MAX_ATTEMPTS:
            return RateLimitDecision(allowed=True, retry_after_seconds=0)

        oldest = datetime.fromisoformat(attempted_at_values[0])
        seconds_remaining = (oldest + WINDOW - moment).total_seconds()
        retry_after_seconds = max(1, math.ceil(seconds_remaining))
        return RateLimitDecision(allowed=False, retry_after_seconds=retry_after_seconds)

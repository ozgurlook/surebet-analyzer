"""Multi-user identity, credential, and session storage.

`AuthRepository` owns the low-level primitives for a small trusted-group
multi-user system: username normalization/validation, Argon2id password
hashing and verification, atomic registration (user + default preferences +
initial session), and cookie-session lifecycle (create/validate/delete).

Higher layers (HTTP routes, rate limiting, CSRF) are implemented on top of
this repository in later tasks and are intentionally out of scope here.
"""

import secrets
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel, ConfigDict

from .db import get_connection

# Reuse the app's existing Argon2id configuration (library defaults) so
# password hashes created before and after this change remain compatible.
_hasher = PasswordHasher()

SESSION_DURATION = timedelta(days=7)

_USERNAME_MIN_LENGTH = 3
_USERNAME_MAX_LENGTH = 24
_USERNAME_EXTRA_CHARS = frozenset("_-.")
_USERNAME_LETTER_NUMBER_CATEGORIES = ("L", "N")

_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 128


class InvalidUsernameError(ValueError):
    """The submitted username fails the length or character rules."""


class InvalidPasswordError(ValueError):
    """The submitted password fails the length rules."""


class DuplicateUsernameError(RuntimeError):
    """The normalized username is already registered."""


class UserRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    username: str
    username_key: str
    active: bool
    created_at: datetime
    updated_at: datetime


class SessionData(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str
    csrf_token: str
    user_id: int
    username: str
    created_at: datetime
    expires_at: datetime


def normalize_username(raw: str) -> tuple[str, str]:
    """Return `(username_display, username_key)` for a raw submitted username.

    `username_display` is the trimmed value as typed. `username_key` is its
    Unicode NFKC form, casefolded, and is the authoritative uniqueness key
    (e.g. "Özgür" and "özgür" collide).
    """
    display = raw.strip()
    key = unicodedata.normalize("NFKC", display).casefold()
    return display, key


def _validate_username(display: str) -> None:
    nfkc_form = unicodedata.normalize("NFKC", display)
    if not (_USERNAME_MIN_LENGTH <= len(nfkc_form) <= _USERNAME_MAX_LENGTH):
        raise InvalidUsernameError(
            f"Username must be {_USERNAME_MIN_LENGTH}-{_USERNAME_MAX_LENGTH} characters."
        )
    for char in nfkc_form:
        if char in _USERNAME_EXTRA_CHARS:
            continue
        if unicodedata.category(char)[0] not in _USERNAME_LETTER_NUMBER_CATEGORIES:
            raise InvalidUsernameError(
                "Username may only contain letters, numbers, '_', '-', and '.'."
            )


def _validate_password(password: str) -> None:
    if not (_PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH):
        raise InvalidPasswordError(
            f"Password must be {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_LENGTH} characters."
        )


def _user_from_row(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        username=row["username_display"],
        username_key=row["username_key"],
        active=bool(row["active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class AuthRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def register_user(
        self, username: str, password: str, now: datetime | None = None
    ) -> tuple[UserRecord, SessionData]:
        """Validate, hash, and atomically create a user, default preferences,
        and an initial session.

        The Argon2id hash is computed before the write transaction opens so
        the (intentionally expensive) hashing work never holds SQLite's write
        lock. The database's `UNIQUE(username_key)` constraint -- not an
        application-level pre-check -- is authoritative for concurrent
        duplicate registrations.
        """
        display, key = normalize_username(username)
        _validate_username(display)
        _validate_password(password)

        # Expensive Argon2id hashing happens before any write transaction opens.
        password_hash = _hasher.hash(password)

        moment = now or datetime.now(UTC)
        timestamp = moment.isoformat()
        expires_at = moment + SESSION_DURATION
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)

        with get_connection(self._db_path) as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO users "
                    "(username_display, username_key, password_hash, active, "
                    "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?);",
                    (display, key, password_hash, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                if "username_key" in str(exc):
                    raise DuplicateUsernameError(
                        f"Username {display!r} is already registered."
                    ) from exc
                raise

            user_id = cursor.lastrowid
            assert user_id is not None

            conn.execute(
                "INSERT INTO user_preferences (user_id, updated_at) VALUES (?, ?);",
                (user_id, timestamp),
            )
            conn.execute(
                "INSERT INTO sessions (token, user_id, csrf_token, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?);",
                (token, user_id, csrf_token, timestamp, expires_at.isoformat()),
            )

        user = UserRecord(
            user_id=user_id,
            username=display,
            username_key=key,
            active=True,
            created_at=moment,
            updated_at=moment,
        )
        session = SessionData(
            token=token,
            csrf_token=csrf_token,
            user_id=user_id,
            username=display,
            created_at=moment,
            expires_at=expires_at,
        )
        return user, session

    def verify_credentials(self, username: str, password: str) -> UserRecord | None:
        """Return the matching active `UserRecord`, or `None` if the username
        is unknown, the password is wrong, or the account is inactive."""
        _, key = normalize_username(username)
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT user_id, username_display, username_key, password_hash, active, "
                "created_at, updated_at FROM users WHERE username_key = ?;",
                (key,),
            ).fetchone()

        if row is None or not row["active"]:
            return None

        try:
            _hasher.verify(row["password_hash"], password)
        except VerifyMismatchError:
            return None

        return _user_from_row(row)

    def create_session(self, user_id: int, now: datetime | None = None) -> SessionData:
        """Create a new seven-day session for `user_id`. Multiple sessions per
        user are allowed."""
        moment = now or datetime.now(UTC)
        expires_at = moment + SESSION_DURATION
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)

        with get_connection(self._db_path) as conn:
            user_row = conn.execute(
                "SELECT username_display FROM users WHERE user_id = ?;", (user_id,)
            ).fetchone()
            if user_row is None:
                raise ValueError(f"Unknown user_id {user_id!r}.")

            conn.execute(
                "INSERT INTO sessions (token, user_id, csrf_token, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?);",
                (token, user_id, csrf_token, moment.isoformat(), expires_at.isoformat()),
            )

        return SessionData(
            token=token,
            csrf_token=csrf_token,
            user_id=user_id,
            username=user_row["username_display"],
            created_at=moment,
            expires_at=expires_at,
        )

    def validate_session(self, token: str, now: datetime | None = None) -> SessionData | None:
        """Return the session if `token` is unexpired and belongs to an active
        user; otherwise `None`. A session belonging to a deactivated user is
        rejected even if its expiry time has not passed."""
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT s.token, s.csrf_token, s.user_id, s.created_at, s.expires_at, "
                "u.username_display, u.active "
                "FROM sessions s JOIN users u ON u.user_id = s.user_id "
                "WHERE s.token = ? AND s.expires_at > ?;",
                (token, moment.isoformat()),
            ).fetchone()

        if row is None or not row["active"]:
            return None

        return SessionData(
            token=row["token"],
            csrf_token=row["csrf_token"],
            user_id=row["user_id"],
            username=row["username_display"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    def delete_session(self, token: str) -> None:
        """Delete only the single session identified by `token` (logout)."""
        with get_connection(self._db_path) as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?;", (token,))

    def cleanup_expired_sessions(self, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?;", (moment.isoformat(),))

    def set_password(self, username: str, password: str, now: datetime | None = None) -> bool:
        """Replace one user's password hash. Returns False when the username is unknown."""
        _, key = normalize_username(username)
        _validate_password(password)
        password_hash = _hasher.hash(password)
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE username_key = ?;",
                (password_hash, moment.isoformat(), key),
            )
        return cursor.rowcount == 1

    def set_user_active(self, user_id: int, active: bool, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE users SET active = ?, updated_at = ? WHERE user_id = ?;",
                (int(active), moment.isoformat(), user_id),
            )

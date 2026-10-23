import secrets
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from surebet.storage.auth import (
    AuthRepository,
    DuplicateUsernameError,
    InvalidPasswordError,
    InvalidUsernameError,
    normalize_username,
)
from surebet.storage.db import get_connection, init_db

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _repo(tmp_path: Path) -> AuthRepository:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return AuthRepository(db_path)


# --- normalize_username -----------------------------------------------------


def test_normalize_username_trims_and_casefolds() -> None:
    assert normalize_username("  Özgür  ") == ("Özgür", "özgür")


def test_normalize_username_applies_nfkc_normalization_to_the_key() -> None:
    display, key = normalize_username("Ｏzgur")  # fullwidth O (U+FF2F)
    assert display == "Ｏzgur"
    assert key == "ozgur"


def test_normalize_username_turkish_and_latin_case_collide() -> None:
    _, ozgur_key = normalize_username("Özgür")
    _, ozgur_lower_key = normalize_username("özgür")
    assert ozgur_key == ozgur_lower_key


# --- registration: username validation --------------------------------------


@pytest.mark.parametrize("username", ["ab", "a" * 25])
def test_register_user_rejects_username_length_outside_3_to_24(
    tmp_path: Path, username: str
) -> None:
    auth = _repo(tmp_path)
    with pytest.raises(InvalidUsernameError):
        auth.register_user(username, "password123", now=FIXED_NOW)


@pytest.mark.parametrize("username", ["abc", "a" * 24])
def test_register_user_accepts_username_length_boundaries(tmp_path: Path, username: str) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user(username, "password123", now=FIXED_NOW)
    assert user.username == username


def test_register_user_accepts_unicode_letters_numbers_and_dash_underscore_dot(
    tmp_path: Path,
) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür_98.test-1", "password123", now=FIXED_NOW)
    assert user.username == "Özgür_98.test-1"


@pytest.mark.parametrize(
    "username",
    ["bad user", "bad\tuser", "bad@user", "bad#user", "bad!user"],
)
def test_register_user_rejects_interior_whitespace_and_other_punctuation(
    tmp_path: Path, username: str
) -> None:
    auth = _repo(tmp_path)
    with pytest.raises(InvalidUsernameError):
        auth.register_user(username, "password123", now=FIXED_NOW)


# --- registration: password validation --------------------------------------


@pytest.mark.parametrize("password", ["short7x", "x" * 129])
def test_register_user_rejects_password_length_outside_8_to_128(
    tmp_path: Path, password: str
) -> None:
    auth = _repo(tmp_path)
    with pytest.raises(InvalidPasswordError):
        auth.register_user("someuser", password, now=FIXED_NOW)


@pytest.mark.parametrize("password", ["eightch8", "x" * 128])
def test_register_user_accepts_password_length_boundaries(tmp_path: Path, password: str) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("someuser", password, now=FIXED_NOW)
    assert user.username == "someuser"


# --- registration: atomicity and duplicate handling -------------------------


def test_register_user_creates_user_preferences_and_session_atomically(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)

    user, session = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    assert user.username == "Özgür"
    assert user.username_key == "özgür"
    assert user.active is True
    assert session.user_id == user.user_id
    assert session.username == "Özgür"
    assert session.expires_at - session.created_at == timedelta(days=7)

    with get_connection(db_path) as conn:
        pref_count = conn.execute(
            "SELECT COUNT(*) FROM user_preferences WHERE user_id = ?;", (user.user_id,)
        ).fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?;", (user.user_id,)
        ).fetchone()[0]
    assert pref_count == 1
    assert session_count == 1


def test_register_user_rejects_case_insensitive_duplicate_turkish_username(
    tmp_path: Path,
) -> None:
    auth = _repo(tmp_path)
    auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    with pytest.raises(DuplicateUsernameError):
        auth.register_user("özgür", "another-password", now=FIXED_NOW)


def test_register_user_concurrent_duplicate_produces_exactly_one_winner(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    results: list[object] = []
    lock = threading.Lock()

    def _attempt(username: str) -> None:
        repo = AuthRepository(db_path)
        try:
            outcome: object = repo.register_user(username, "password123", now=FIXED_NOW)
        except DuplicateUsernameError as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=_attempt, args=(name,)) for name in ("racer", "RACER", "Racer")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [r for r in results if isinstance(r, tuple)]
    failures = [r for r in results if isinstance(r, DuplicateUsernameError)]
    assert len(successes) == 1
    assert len(failures) == 2

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE username_key = 'racer';").fetchone()[
            0
        ]
    assert count == 1


# --- verify_credentials -------------------------------------------------------


def test_verify_credentials_accepts_correct_password_case_insensitive_username(
    tmp_path: Path,
) -> None:
    auth = _repo(tmp_path)
    auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    verified = auth.verify_credentials("özgür", "test-password")
    assert verified is not None
    assert verified.username == "Özgür"


def test_verify_credentials_rejects_wrong_password(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    assert auth.verify_credentials("özgür", "wrong-password") is None


def test_verify_credentials_rejects_unknown_username(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    assert auth.verify_credentials("nobody", "whatever1") is None


def test_verify_credentials_rejects_inactive_user(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    auth.set_user_active(user.user_id, False, now=FIXED_NOW)

    assert auth.verify_credentials("özgür", "test-password") is None


# --- sessions -----------------------------------------------------------------


def test_create_session_allows_multiple_sessions_per_user(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    session_a = auth.create_session(user.user_id, now=FIXED_NOW)
    session_b = auth.create_session(user.user_id, now=FIXED_NOW)

    assert session_a.token != session_b.token
    assert auth.validate_session(session_a.token, now=FIXED_NOW) is not None
    assert auth.validate_session(session_b.token, now=FIXED_NOW) is not None


def test_create_session_expires_after_seven_days(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    session = auth.create_session(user.user_id, now=FIXED_NOW)
    assert session.expires_at - session.created_at == timedelta(days=7)


def test_validate_session_rejects_expired_token(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)
    session = auth.create_session(user.user_id, now=FIXED_NOW)

    past_expiry = FIXED_NOW + timedelta(days=7, seconds=1)
    assert auth.validate_session(session.token, now=past_expiry) is None


def test_validate_session_rejects_unknown_token(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    assert auth.validate_session("does-not-exist", now=FIXED_NOW) is None


def test_delete_session_removes_only_that_session(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)
    session_a = auth.create_session(user.user_id, now=FIXED_NOW)
    session_b = auth.create_session(user.user_id, now=FIXED_NOW)

    auth.delete_session(session_a.token)

    assert auth.validate_session(session_a.token, now=FIXED_NOW) is None
    assert auth.validate_session(session_b.token, now=FIXED_NOW) is not None


def test_validate_session_rejects_inactive_user_even_before_expiry(
    tmp_path: Path,
) -> None:
    auth = _repo(tmp_path)
    user, session = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    auth.set_user_active(user.user_id, False, now=FIXED_NOW)

    assert auth.validate_session(session.token, now=FIXED_NOW) is None


def test_cleanup_expired_sessions_deletes_only_expired_rows(tmp_path: Path) -> None:
    auth = _repo(tmp_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)
    live = auth.create_session(user.user_id, now=FIXED_NOW)
    expiring_soon = auth.create_session(
        user.user_id, now=FIXED_NOW - timedelta(days=6, hours=23, minutes=59)
    )

    cleanup_at = FIXED_NOW + timedelta(minutes=2)
    auth.cleanup_expired_sessions(now=cleanup_at)

    assert auth.validate_session(live.token, now=cleanup_at) is not None
    assert auth.validate_session(expiring_soon.token, now=cleanup_at) is None


# --- set_user_active ------------------------------------------------------------


def test_set_user_active_updates_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    user, _ = auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    auth.set_user_active(user.user_id, False, now=FIXED_NOW)

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT active FROM users WHERE user_id = ?;", (user.user_id,)
        ).fetchone()
    assert row["active"] == 0


# --- password hashing must not use plaintext storage ----------------------------


def test_password_hash_is_not_the_plaintext_password(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    auth.register_user("Özgür", "test-password", now=FIXED_NOW)

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username_key = 'özgür';"
        ).fetchone()
    assert row["password_hash"] != "test-password"
    assert row["password_hash"].startswith("$argon2id$")


def test_set_password_replaces_the_hash_for_a_known_user_only(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = AuthRepository(db_path)
    first, second = secrets.token_urlsafe(12), secrets.token_urlsafe(12)
    repo.register_user("Zeynep", first)

    assert repo.set_password("zeynep", second) is True
    assert repo.verify_credentials("zeynep", first) is None
    assert repo.verify_credentials("zeynep", second) is not None
    assert repo.set_password("nobody", second) is False

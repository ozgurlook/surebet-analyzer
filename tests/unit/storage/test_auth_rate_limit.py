from datetime import UTC, datetime, timedelta
from pathlib import Path

from surebet.storage.auth_rate_limit import (
    AuthAction,
    AuthRateLimitRepository,
    RateLimitDecision,
)
from surebet.storage.db import get_connection, init_db

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)


def _repo(tmp_path: Path) -> AuthRateLimitRepository:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return AuthRateLimitRepository(db_path)


def _fail(
    repo: AuthRateLimitRepository,
    client_ip: str,
    action: AuthAction,
    times: int,
    now: datetime = FIXED_NOW,
) -> RateLimitDecision:
    decision = RateLimitDecision(allowed=True, retry_after_seconds=0)
    for _ in range(times):
        decision = repo.record_failure(client_ip, action, now=now)
    return decision


# --- check()/record_failure() basic allow/block behavior --------------------


def test_check_allows_when_no_prior_attempts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    decision = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    assert decision.allowed is True
    assert decision.retry_after_seconds == 0


def test_check_still_allowed_after_four_failures(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for _ in range(4):
        repo.record_failure("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    check = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    assert check.allowed is True
    assert check.retry_after_seconds == 0


def test_sixth_attempt_is_blocked_after_five_recorded_failures(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for _ in range(4):
        repo.record_failure("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)

    # The 5th failure itself was allowed to happen (checked beforehand); once
    # it is recorded, the bucket holds 5 failures and the *next* attempt
    # (the 6th) must be blocked.
    fifth = repo.record_failure("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    assert fifth.allowed is False

    check_before_sixth = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    assert check_before_sixth.allowed is False


def test_retry_after_seconds_is_bounded_positive_when_blocked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    decision = _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5)
    assert decision.allowed is False
    assert decision.retry_after_seconds > 0
    assert decision.retry_after_seconds <= int(WINDOW.total_seconds())


def test_retry_after_seconds_counts_down_toward_window_expiry(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5, now=FIXED_NOW)

    just_after = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW + timedelta(seconds=1))
    much_later = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW + timedelta(minutes=10))
    assert just_after.allowed is False
    assert much_later.allowed is False
    assert much_later.retry_after_seconds < just_after.retry_after_seconds


def test_attempt_becomes_allowed_again_after_window_rolls_past(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5, now=FIXED_NOW)

    still_blocked = repo.check(
        "1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW + WINDOW - timedelta(seconds=1)
    )
    now_allowed = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW + WINDOW)
    assert still_blocked.allowed is False
    assert now_allowed.allowed is True
    assert now_allowed.retry_after_seconds == 0


# --- separate buckets: action and IP -----------------------------------------


def test_login_and_register_buckets_are_independent_per_ip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5)

    login_check = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    register_check = repo.check("1.2.3.4", AuthAction.REGISTER, now=FIXED_NOW)
    assert login_check.allowed is False
    assert register_check.allowed is True


def test_different_ips_do_not_affect_each_others_buckets(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5)

    blocked_ip_check = repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    other_ip_check = repo.check("5.6.7.8", AuthAction.LOGIN, now=FIXED_NOW)
    assert blocked_ip_check.allowed is False
    assert other_ip_check.allowed is True


# --- clear() ------------------------------------------------------------------


def test_clear_resets_only_the_matching_ip_and_action_bucket(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fail(repo, "1.2.3.4", AuthAction.LOGIN, times=5)
    _fail(repo, "1.2.3.4", AuthAction.REGISTER, times=5)
    _fail(repo, "5.6.7.8", AuthAction.LOGIN, times=5)

    repo.clear("1.2.3.4", AuthAction.LOGIN)

    assert repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW).allowed is True
    # Sibling buckets are untouched by the clear.
    assert repo.check("1.2.3.4", AuthAction.REGISTER, now=FIXED_NOW).allowed is False
    assert repo.check("5.6.7.8", AuthAction.LOGIN, now=FIXED_NOW).allowed is False


def test_clear_on_empty_bucket_is_a_no_op(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.clear("1.2.3.4", AuthAction.LOGIN)  # must not raise
    assert repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW).allowed is True


# --- persistence across repository instances ----------------------------------


def test_state_persists_across_fresh_repository_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)

    first_repo = AuthRateLimitRepository(db_path)
    _fail(first_repo, "1.2.3.4", AuthAction.LOGIN, times=5)

    second_repo = AuthRateLimitRepository(db_path)
    decision = second_repo.check("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)
    assert decision.allowed is False


# --- cleanup_expired() ---------------------------------------------------------


def test_cleanup_expired_deletes_rows_older_than_the_window(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    repo = AuthRateLimitRepository(db_path)
    init_db(db_path)
    repo.record_failure("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)

    repo.cleanup_expired(now=FIXED_NOW + WINDOW + timedelta(seconds=1))

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM auth_rate_limit_attempts;").fetchone()[0]
    assert count == 0


def test_cleanup_expired_keeps_rows_still_within_the_window(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    repo = AuthRateLimitRepository(db_path)
    init_db(db_path)
    repo.record_failure("1.2.3.4", AuthAction.LOGIN, now=FIXED_NOW)

    repo.cleanup_expired(now=FIXED_NOW + timedelta(minutes=5))

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM auth_rate_limit_attempts;").fetchone()[0]
    assert count == 1


# --- table never stores sensitive data -----------------------------------------


def test_table_only_stores_client_ip_action_and_timestamp_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(auth_rate_limit_attempts);")}
    assert columns == {"attempt_id", "client_ip", "action", "attempted_at"}

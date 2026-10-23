import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest

from surebet.domain.enums import MarketType, Outcome, Source
from surebet.notifications.dispatcher import MAX_ATTEMPTS, TelegramOutboxDispatcher
from surebet.notifications.models import (
    CardState,
    DualPolicyEvaluation,
    LifecycleStatus,
    OutboxActionDraft,
    OutboxActionType,
    TelegramFailureKind,
    TelegramResult,
    card_key,
)
from surebet.opportunities.strategy import CalculatedOpportunity, StrategySelection
from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.storage.notifications import NotificationRepository, TelegramSettingsUpdate

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
USER_ID = 1
CHAT_ID = 123
MATCHED_ID = "event-1"
BUDGET_CENTS = 1_000_000

_FAKE_PASSWORD = "correct-horse-battery-staple"
_usernames = (f"dispatcher-user-{n}" for n in count())


class _StepClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def __call__(self) -> datetime:
        return self._current

    def advance(self, seconds: int) -> None:
        self._current += timedelta(seconds=seconds)


class _FakeDispatcherTelegramClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.edited: list[dict[str, object]] = []
        self._failure: tuple[TelegramFailureKind, str] | None = None
        self._message_counter = 100

    def fail_with(self, kind: TelegramFailureKind, safe_error: str) -> None:
        self._failure = (kind, safe_error)

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        silent: bool = False,
        reply_markup: dict | None = None,
    ) -> TelegramResult:
        if self._failure is not None:
            kind, err = self._failure
            return TelegramResult(ok=False, failure_kind=kind, safe_error=err)
        self._message_counter += 1
        msg = {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "silent": silent,
            "reply_markup": reply_markup,
            "message_id": self._message_counter,
        }
        self.sent.append(msg)
        return TelegramResult(ok=True, message_id=self._message_counter)

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> TelegramResult:
        if self._failure is not None:
            kind, err = self._failure
            return TelegramResult(ok=False, failure_kind=kind, safe_error=err)
        msg = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": reply_markup,
        }
        self.edited.append(msg)
        return TelegramResult(ok=True, message_id=message_id)


@pytest.fixture
def client() -> _FakeDispatcherTelegramClient:
    return _FakeDispatcherTelegramClient()


@pytest.fixture
def repo(tmp_path: Path) -> NotificationRepository:
    db_path = tmp_path / "dispatcher_test.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    r = NotificationRepository(db_path)
    user, _ = auth.register_user("dispatcher-test-user", _FAKE_PASSWORD)
    r.update_settings(user.user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    r.mark_chat_validated(user.user_id, CHAT_ID, NOW)
    return r


def _card_state(
    matched_id: str,
    *,
    user_id: int = USER_ID,
    chat_id: int = CHAT_ID,
    message_id: int | None = None,
) -> CardState:
    ck = card_key(matched_id, MarketType.FULL_TIME_RESULT)
    opp = _opportunity(100)
    eval_obj = DualPolicyEvaluation(
        matched_id=matched_id,
        market_type=MarketType.FULL_TIME_RESULT,
        selected=opp,
        draw_only=opp,
        uses_source_a_match_sides=False,
        relevant_sources=frozenset({Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_A}),
    )
    return CardState(
        user_id=user_id,
        chat_id=chat_id,
        card_key=ck,
        matched_id=matched_id,
        market_type=MarketType.FULL_TIME_RESULT,
        generation=1,
        status=LifecycleStatus.ACTIVE,
        message_id=message_id,
        evaluation=eval_obj,
        first_profit_bps=100,
        current_profit_bps=100,
        peak_profit_bps=100,
        anchor_bps=100,
        last_alert_at=None,
        below_since=None,
        closed_at=None,
        revival_count=0,
        first_seen_at=NOW,
        last_seen_at=NOW,
        last_edit_at=None,
        rendered_fingerprint=None,
    )


def _enqueue(repo: NotificationRepository, count: int = 1) -> None:
    transitions = []
    for i in range(count):
        m_id = f"event-test-{i}"
        state = _card_state(m_id)
        draft = OutboxActionDraft(
            action=OutboxActionType.SEND_CARD,
            text=f"card content {i}",
            fingerprint=f"fp-{i}",
            next_attempt_at=NOW,
            silent=False,
        )
        transitions.append((state, (draft,)))
    repo.apply_transitions(transitions)


def _enqueue_sunset(repo: NotificationRepository, message_id: int = 4242) -> None:
    state = _card_state("event-sunset")
    draft = OutboxActionDraft(
        action=OutboxActionType.EDIT_CARD,
        message_id=message_id,
        text="sunset edit content",
        fingerprint="fp-sunset",
        next_attempt_at=NOW,
        silent=True,
    )
    repo.apply_transitions([(state, (draft,))])


def _selection(outcome: Outcome, source: Source, odds: str) -> StrategySelection:
    return StrategySelection(
        outcome=outcome,
        source=source,
        odds=Decimal(odds),
        stake_cents=1_000,
        cashback_eligible=source in (Source.SOURCE_C, Source.SOURCE_B),
    )


def _opportunity(profit_bps: int) -> CalculatedOpportunity:
    profit_cents = profit_bps * BUDGET_CENTS // 10_000
    return CalculatedOpportunity(
        opportunity_id=f"event-1:{profit_bps}",
        matched_id=MATCHED_ID,
        competition="Premier League",
        country_id="country:GB",
        country_name="United Kingdom",
        country_iso_code="GB",
        league_id="league:00000000-0000-4000-8000-000000000002",
        league_name="Premier League",
        home="Arsenal",
        away="Chelsea",
        kickoff=KICKOFF,
        budget_cents=BUDGET_CENTS,
        include_source_a_match_sides=False,
        selections=MappingProxyType(
            {
                Outcome.HOME: _selection(Outcome.HOME, Source.SOURCE_C, "2.10"),
                Outcome.DRAW: _selection(Outcome.DRAW, Source.SOURCE_A, "3.40"),
                Outcome.AWAY: _selection(Outcome.AWAY, Source.SOURCE_B, "2.80"),
            }
        ),
        scenarios=MappingProxyType({}),
        min_adjusted_profit_cents=profit_cents,
        min_adjusted_profit_percent=round(profit_bps / 100, 2),
        min_standard_profit_cents=profit_cents,
        min_standard_profit_percent=round(profit_bps / 100, 2),
        is_positive=profit_cents > 0,
        oldest_quote_age_seconds=10,
        calculated_at=NOW,
    )


def _state(*, user_id: int, chat_id: int = CHAT_ID, message_id: int | None = None) -> CardState:
    return _card_state(MATCHED_ID, user_id=user_id, chat_id=chat_id, message_id=message_id)


def _current_card(repo: NotificationRepository, user_id: int) -> CardState | None:
    key = card_key(MATCHED_ID, MarketType.FULL_TIME_RESULT)
    return repo.list_current_cards(user_id).get(key)


def _create_user(auth: AuthRepository) -> int:
    user, _session = auth.register_user(next(_usernames), _FAKE_PASSWORD)
    return user.user_id


def repo_with_due_action(
    tmp_path: Path, *, chat_id: int = CHAT_ID
) -> tuple[NotificationRepository, int]:
    db_path = tmp_path / "dispatcher.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_id = _create_user(auth)
    repo.update_settings(user_id, TelegramSettingsUpdate(enabled=True, chat_id=chat_id))
    repo.mark_chat_validated(user_id, chat_id, NOW)
    repo.apply_transitions(
        [
            (
                _state(user_id=user_id, chat_id=chat_id),
                (
                    OutboxActionDraft(
                        action=OutboxActionType.SEND_CARD,
                        text="full card",
                        fingerprint="fp-1",
                        next_attempt_at=NOW,
                    ),
                ),
            )
        ]
    )
    return repo, user_id


def repo_with_due_edit(tmp_path: Path, *, message_id: int) -> tuple[NotificationRepository, int]:
    db_path = tmp_path / "dispatcher.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_id = _create_user(auth)
    repo.update_settings(user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_id, CHAT_ID, NOW)
    repo.apply_transitions(
        [
            (
                _state(user_id=user_id, message_id=message_id),
                (
                    OutboxActionDraft(
                        action=OutboxActionType.EDIT_CARD,
                        text="edited card",
                        fingerprint="fp-2",
                        next_attempt_at=NOW,
                    ),
                ),
            )
        ]
    )
    return repo, user_id


def rate_limited(seconds: int) -> TelegramResult:
    return TelegramResult(
        ok=False,
        failure_kind=TelegramFailureKind.RATE_LIMITED,
        retry_after_seconds=seconds,
        safe_error="Telegram rate limited",
    )


def message_not_found() -> TelegramResult:
    return TelegramResult(
        ok=False,
        failure_kind=TelegramFailureKind.MESSAGE_NOT_FOUND,
        safe_error="Telegram message not found",
    )


def delivered(message_id: int = 42) -> TelegramResult:
    return TelegramResult(ok=True, message_id=message_id)


@pytest.mark.asyncio
async def test_dispatcher_honors_telegram_retry_after(tmp_path: Path) -> None:
    repo, _user_id = repo_with_due_action(tmp_path)
    client = AsyncMock(send_text=AsyncMock(return_value=rate_limited(17)))

    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    assert repo.list_due_actions(NOW + timedelta(seconds=16)) == ()
    assert len(repo.list_due_actions(NOW + timedelta(seconds=17))) == 1


@pytest.mark.asyncio
async def test_missing_edit_target_queues_one_replacement_send(tmp_path: Path) -> None:
    repo, _user_id = repo_with_due_edit(tmp_path, message_id=42)
    client = AsyncMock(edit_text=AsyncMock(return_value=message_not_found()))

    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    actions = repo.list_due_actions(NOW)
    assert [item.action for item in actions] == [OutboxActionType.SEND_CARD]


@pytest.mark.asyncio
async def test_successful_send_marks_delivered_and_records_message_id(tmp_path: Path) -> None:
    repo, user_id = repo_with_due_action(tmp_path)
    client = AsyncMock(send_text=AsyncMock(return_value=delivered(999)))

    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    assert repo.list_due_actions(NOW) == ()
    current = _current_card(repo, user_id)
    assert current is not None
    assert current.message_id == 999
    settings = repo.get_settings(user_id)
    assert settings.last_successful_delivery_at == NOW


@pytest.mark.asyncio
async def test_transient_failure_uses_exponential_backoff(tmp_path: Path) -> None:
    repo, _user_id = repo_with_due_action(tmp_path)
    client = AsyncMock(
        send_text=AsyncMock(
            return_value=TelegramResult(
                ok=False, failure_kind=TelegramFailureKind.TRANSIENT, safe_error="Telegram HTTP 500"
            )
        )
    )

    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    assert repo.list_due_actions(NOW) == ()
    assert len(repo.list_due_actions(NOW + timedelta(seconds=1))) == 1


@pytest.mark.asyncio
async def test_run_once_reads_at_most_fifty_due_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "dispatcher.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_id = _create_user(auth)
    repo.update_settings(user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_id, CHAT_ID, NOW)
    for index in range(60):
        state = _card_state(f"event-{index}", user_id=user_id)
        repo.apply_transitions(
            [
                (
                    state,
                    (
                        OutboxActionDraft(
                            action=OutboxActionType.SEND_CARD,
                            text=f"card {index}",
                            fingerprint=f"fp-{index}",
                            next_attempt_at=NOW,
                        ),
                    ),
                )
            ]
        )

    client = AsyncMock(send_text=AsyncMock(return_value=delivered(1)))
    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW, chat_budget=100).run_once()

    assert client.send_text.await_count == 50
    assert len(repo.list_due_actions(NOW, limit=100)) == 10


@pytest.mark.asyncio
async def test_one_actions_unexpected_exception_does_not_block_the_next_due_action(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec section 11: one user's delivery failure cannot disable or delay
    another user's durable state. Normal Telegram failures are returned as
    `TelegramResult`s (never raised), but `run_once` must still isolate each
    action from an unexpected exception (e.g. a bug, or an unhandled network
    error) so a single bad action cannot silently starve the rest of the
    batch."""
    db_path = tmp_path / "dispatcher.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_raising, _ = auth.register_user(next(_usernames), _FAKE_PASSWORD)
    user_ok, _ = auth.register_user(next(_usernames), _FAKE_PASSWORD)

    for user, offset in ((user_raising, 0), (user_ok, 1)):
        repo.update_settings(user.user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
        repo.mark_chat_validated(user.user_id, CHAT_ID, NOW)
        repo.apply_transitions(
            [
                (
                    _state(user_id=user.user_id, chat_id=CHAT_ID),
                    (
                        OutboxActionDraft(
                            action=OutboxActionType.SEND_CARD,
                            text=f"card for {user.user_id}",
                            fingerprint=f"fp-{user.user_id}",
                            # user_raising's action is due strictly earlier, so
                            # list_due_actions (ORDER BY next_attempt_at ASC) always
                            # processes it first -- proving the *next* action still
                            # runs despite the earlier one raising.
                            next_attempt_at=NOW - timedelta(seconds=1) + timedelta(seconds=offset),
                        ),
                    ),
                )
            ]
        )

    async def fake_send_text(*, chat_id: int, text: str, **kwargs: object) -> TelegramResult:
        if str(user_raising.user_id) in text:
            raise RuntimeError("boom: unexpected client failure")
        return delivered(555)

    client = AsyncMock(send_text=AsyncMock(side_effect=fake_send_text))

    with caplog.at_level("ERROR", logger="surebet.notifications.dispatcher"):
        await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    # user_ok's action was still delivered despite user_raising's exception.
    ok_state = _current_card(repo, user_ok.user_id)
    assert ok_state is not None and ok_state.message_id == 555
    assert repo.get_settings(user_ok.user_id).last_successful_delivery_at == NOW

    # The exception was logged, not silently swallowed.
    assert "boom: unexpected client failure" in caplog.text


@pytest.mark.asyncio
async def test_start_and_stop_manage_a_cancellable_background_loop(tmp_path: Path) -> None:
    repo, _user_id = repo_with_due_action(tmp_path)
    client = AsyncMock(send_text=AsyncMock(return_value=delivered(1)))
    dispatcher = TelegramOutboxDispatcher(
        repo, client, clock=lambda: NOW, poll_interval_seconds=0.01
    )

    await dispatcher.start()
    await dispatcher.start()  # idempotent: starting twice must not raise or spawn twice
    import asyncio

    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert client.send_text.await_count >= 1


@pytest.mark.asyncio
async def test_one_users_delivery_failure_is_isolated_from_another_users_success(
    tmp_path: Path,
) -> None:
    """Two users share one Chat ID. One's send fails (and gets rescheduled with
    an error recorded); the other's succeeds. Neither user's settings row or
    outbox row may be affected by the other's outcome, even though `chat_id`
    is identical for both -- isolation must come entirely from `user_id`."""
    db_path = tmp_path / "dispatcher.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_ok, _ = auth.register_user(next(_usernames), _FAKE_PASSWORD)
    user_fail, _ = auth.register_user(next(_usernames), _FAKE_PASSWORD)

    for user in (user_ok, user_fail):
        repo.update_settings(user.user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
        repo.mark_chat_validated(user.user_id, CHAT_ID, NOW)
        repo.apply_transitions(
            [
                (
                    _state(user_id=user.user_id, chat_id=CHAT_ID),
                    (
                        OutboxActionDraft(
                            action=OutboxActionType.SEND_CARD,
                            text=f"card for {user.user_id}",
                            fingerprint=f"fp-{user.user_id}",
                            next_attempt_at=NOW,
                        ),
                    ),
                )
            ]
        )

    async def fake_send_text(*, chat_id: int, text: str, **kwargs: object) -> TelegramResult:
        if str(user_ok.user_id) in text:
            return delivered(777)
        return TelegramResult(
            ok=False, failure_kind=TelegramFailureKind.TRANSIENT, safe_error="Telegram HTTP 500"
        )

    client = AsyncMock(send_text=AsyncMock(side_effect=fake_send_text))
    await TelegramOutboxDispatcher(repo, client, clock=lambda: NOW).run_once()

    ok_settings = repo.get_settings(user_ok.user_id)
    fail_settings = repo.get_settings(user_fail.user_id)

    assert ok_settings.last_successful_delivery_at == NOW
    assert ok_settings.last_error is None

    assert fail_settings.last_successful_delivery_at is None
    assert fail_settings.last_error is not None

    # The succeeding user's action is delivered; the failing user's is
    # rescheduled (still due after backoff) -- neither state leaks to the other.
    assert repo.list_due_actions(NOW) == ()
    still_due = repo.list_due_actions(NOW + timedelta(seconds=1))
    assert len(still_due) == 1
    assert still_due[0].user_id == user_fail.user_id

    ok_state = _current_card(repo, user_ok.user_id)
    fail_state = _current_card(repo, user_fail.user_id)
    assert ok_state is not None and ok_state.message_id == 777
    assert fail_state is not None and fail_state.message_id is None


@pytest.mark.asyncio
async def test_dispatcher_wake_breaks_poll_sleep_immediately(tmp_path: Path) -> None:
    db_path = tmp_path / "wake.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)
    user_id = _create_user(auth)
    repo.update_settings(user_id, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_id, CHAT_ID, NOW)

    client = AsyncMock(send_text=AsyncMock(return_value=delivered(1)))
    dispatcher = TelegramOutboxDispatcher(
        repo, client, clock=lambda: NOW, poll_interval_seconds=60.0
    )

    await dispatcher.start()
    try:
        # Initially outbox is empty
        assert client.send_text.call_count == 0

        # Now queue a draft
        repo.apply_transitions(
            [
                (
                    _state(user_id=user_id),
                    (
                        OutboxActionDraft(
                            action=OutboxActionType.SEND_CARD,
                            text="wake test",
                            fingerprint="fp-wake",
                            next_attempt_at=NOW,
                        ),
                    ),
                )
            ]
        )

        # Wake up immediately (do not wait 60s)
        dispatcher.wake()
        # Give asyncio event loop a tiny moment to process
        await asyncio.sleep(0.05)

        assert client.send_text.call_count == 1
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_delivers_different_chats_concurrently(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    repo = NotificationRepository(db_path)

    u1 = _create_user(auth)
    u2 = _create_user(auth)
    repo.update_settings(u1, TelegramSettingsUpdate(enabled=True, chat_id=111))
    repo.mark_chat_validated(u1, 111, NOW)
    repo.update_settings(u2, TelegramSettingsUpdate(enabled=True, chat_id=222))
    repo.mark_chat_validated(u2, 222, NOW)

    repo.apply_transitions(
        [
            (
                _state(user_id=u1, chat_id=111),
                (
                    OutboxActionDraft(
                        action=OutboxActionType.SEND_CARD,
                        text="msg user 1",
                        fingerprint="fp-u1",
                        next_attempt_at=NOW,
                    ),
                ),
            )
        ]
    )
    repo.apply_transitions(
        [
            (
                _state(user_id=u2, chat_id=222),
                (
                    OutboxActionDraft(
                        action=OutboxActionType.SEND_CARD,
                        text="msg user 2",
                        fingerprint="fp-u2",
                        next_attempt_at=NOW,
                    ),
                ),
            )
        ]
    )

    active_concurrent = 0
    max_concurrent = 0

    async def slow_send(*, chat_id: int, text: str, **kwargs: object) -> TelegramResult:
        nonlocal active_concurrent, max_concurrent
        active_concurrent += 1
        max_concurrent = max(max_concurrent, active_concurrent)
        await asyncio.sleep(0.05)
        active_concurrent -= 1
        return delivered(chat_id)

    client = AsyncMock(send_text=AsyncMock(side_effect=slow_send))
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)
    await dispatcher.run_once()

    # Confirms both chats were in-flight at the same time
    assert max_concurrent == 2
    assert client.send_text.call_count == 2


@pytest.mark.asyncio
async def test_chat_budget_defers_the_overflow_instead_of_sending_it(repo, client) -> None:
    _enqueue(repo, count=20)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW, chat_budget=15)

    await dispatcher.run_once()

    assert len(client.sent) == 15
    assert len(repo.list_due_actions(NOW + timedelta(seconds=60))) == 5


@pytest.mark.asyncio
async def test_the_budget_refills_after_the_window(repo, client) -> None:
    _enqueue(repo, count=20)
    clock = _StepClock(NOW)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=clock, chat_budget=15)

    await dispatcher.run_once()
    clock.advance(seconds=61)
    await dispatcher.run_once()

    assert len(client.sent) == 20


@pytest.mark.asyncio
async def test_a_permanent_failure_is_dead_lettered_after_one_attempt(repo, client) -> None:
    _enqueue(repo, count=1)
    client.fail_with(TelegramFailureKind.PERMANENT, "Telegram HTTP 400")
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    assert repo.list_due_actions(NOW + timedelta(hours=1)) == ()


@pytest.mark.asyncio
async def test_transient_failures_stop_after_max_attempts(repo, client) -> None:
    _enqueue(repo, count=1)
    client.fail_with(TelegramFailureKind.TRANSIENT, "Telegram timeout")
    clock = _StepClock(NOW)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=clock)

    for _ in range(9):
        await dispatcher.run_once()
        clock.advance(seconds=600)

    assert repo.list_due_actions(clock() + timedelta(hours=1)) == ()


@pytest.mark.asyncio
async def test_an_unreachable_chat_disables_the_user(repo, client) -> None:
    _enqueue(repo, count=3)
    client.fail_with(TelegramFailureKind.CHAT_UNREACHABLE, "blocked")
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    assert repo.get_settings(USER_ID).enabled is False
    assert repo.list_due_actions(NOW + timedelta(hours=1)) == ()


@pytest.mark.asyncio
async def test_an_explicit_message_id_skips_the_card_lookup(repo, client) -> None:
    _enqueue_sunset(repo, message_id=4242)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    assert client.edited[-1]["message_id"] == 4242


# ---------------------------------------------------------------------------
# Final-review regressions: C1 (reply ids), I2 (deferral attempts), I3 (retries)
# ---------------------------------------------------------------------------


def _enqueue_reply(
    repo: NotificationRepository,
    action: OutboxActionType,
    *,
    matched_id: str,
    card_message_id: int,
) -> None:
    """Queue a threaded reply under an already-delivered card."""
    state = _card_state(matched_id, message_id=card_message_id)
    draft = OutboxActionDraft(
        action=action,
        text="🟡 %2,40 → %2,15",
        fingerprint=f"fp-{action.value}",
        next_attempt_at=NOW,
        message_id=card_message_id,
    )
    repo.apply_transitions([(state, (draft,))])


@pytest.mark.asyncio
async def test_delivering_an_alert_reply_leaves_the_cards_message_id_alone(repo, client) -> None:
    """C1: the reply's own message id must never become the card's message id."""
    _enqueue_reply(repo, OutboxActionType.SEND_ALERT, matched_id="event-alert", card_message_id=555)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    reply_message_id = client.sent[-1]["message_id"]
    assert reply_message_id != 555  # Telegram created a brand new message
    card = repo.list_current_cards(USER_ID)[card_key("event-alert", MarketType.FULL_TIME_RESULT)]
    assert card.message_id == 555


@pytest.mark.asyncio
async def test_delivering_a_closing_reply_leaves_the_cards_message_id_alone(repo, client) -> None:
    _enqueue_reply(
        repo, OutboxActionType.SEND_CLOSED, matched_id="event-closed", card_message_id=556
    )
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    assert client.sent[-1]["message_id"] != 556
    card = repo.list_current_cards(USER_ID)[card_key("event-closed", MarketType.FULL_TIME_RESULT)]
    assert card.message_id == 556


@pytest.mark.asyncio
async def test_a_budget_deferral_does_not_burn_a_retry_attempt(repo, client) -> None:
    """I2: a row merely waiting its turn must not inflate its own retry counter."""
    _enqueue(repo, count=2)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW, chat_budget=1)

    await dispatcher.run_once()

    [deferred] = repo.list_due_actions(NOW + timedelta(seconds=60))
    assert deferred.attempt_count == 0


@pytest.mark.asyncio
async def test_a_genuine_transient_failure_does_burn_a_retry_attempt(repo, client) -> None:
    _enqueue(repo, count=1)
    client.fail_with(TelegramFailureKind.TRANSIENT, "Telegram timeout")
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    [record] = repo.list_due_actions(NOW + timedelta(hours=1))
    assert record.attempt_count == 1


@pytest.mark.asyncio
async def test_a_coalesced_rewrite_keeps_the_retry_budget(repo, client) -> None:
    """I3: refreshing a pending row's text must not restart its retry budget."""
    _enqueue(repo, count=1)
    client.fail_with(TelegramFailureKind.TRANSIENT, "Telegram timeout")
    clock = _StepClock(NOW)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=clock)

    for _ in range(3):
        await dispatcher.run_once()
        clock.advance(seconds=600)
        _enqueue(repo, count=1)  # same coalesce key, freshly rendered payload

    [record] = repo.list_due_actions(clock() + timedelta(hours=1))
    assert record.attempt_count == 3


@pytest.mark.asyncio
async def test_a_repeatedly_coalesced_row_still_reaches_the_dead_letter(repo, client) -> None:
    """The row itself must reach MAX_ATTEMPTS however often its text is refreshed."""
    _enqueue(repo, count=1)
    [original] = repo.list_due_actions(NOW)
    client.fail_with(TelegramFailureKind.TRANSIENT, "Telegram timeout")
    clock = _StepClock(NOW)
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=clock)

    for _ in range(MAX_ATTEMPTS):
        await dispatcher.run_once()
        clock.advance(seconds=600)
        _enqueue(repo, count=1)  # same coalesce key, freshly rendered payload

    still_pending = repo.list_due_actions(clock() + timedelta(hours=1))
    assert all(record.outbox_id != original.outbox_id for record in still_pending)


@pytest.mark.asyncio
async def test_a_missing_edit_target_clears_the_cards_dead_message_id(repo, client) -> None:
    """I3: without this the next cycle re-drafts an EDIT_CARD at the dead id forever."""
    state = _card_state("event-gone", message_id=4242)
    draft = OutboxActionDraft(
        action=OutboxActionType.EDIT_CARD,
        text="card content",
        fingerprint="fp-gone",
        next_attempt_at=NOW,
        message_id=4242,
    )
    repo.apply_transitions([(state, (draft,))])
    client.fail_with(TelegramFailureKind.MESSAGE_NOT_FOUND, "message to edit not found")
    dispatcher = TelegramOutboxDispatcher(repo, client, clock=lambda: NOW)

    await dispatcher.run_once()

    card = repo.list_current_cards(USER_ID)[card_key("event-gone", MarketType.FULL_TIME_RESULT)]
    assert card.message_id is None

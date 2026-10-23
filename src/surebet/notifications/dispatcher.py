"""Delivers due Telegram outbox actions with retry/backoff and coalescing consumption."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from surebet.storage.notifications import NotificationRepository

from .models import (
    OutboxActionDraft,
    OutboxActionType,
    OutboxRecord,
    TelegramFailureKind,
    TelegramResult,
)
from .telegram import TelegramClient

logger = logging.getLogger("surebet.notifications.dispatcher")

_MAX_BACKOFF_SECONDS = 300
_MAX_RATE_LIMIT_SECONDS = 3600
MAX_ATTEMPTS = 8
_SEND_ACTIONS = (
    OutboxActionType.SEND_CARD,
    OutboxActionType.SEND_ALERT,
    OutboxActionType.SEND_REVIVED,
    OutboxActionType.SEND_CLOSED,
)


class _ChatBudget:
    """Telegram allows roughly 20 messages a minute per chat; edits count too."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = timedelta(seconds=window_seconds)
        self._spent: dict[int, list[datetime]] = defaultdict(list)

    def take(self, chat_id: int, now: datetime) -> bool:
        recent = [stamp for stamp in self._spent[chat_id] if now - stamp < self._window]
        self._spent[chat_id] = recent
        if len(recent) >= self._limit:
            return False
        recent.append(now)
        return True


class TelegramOutboxDispatcher:
    """Runs as an independent background loop; its failures never affect collection."""

    def __init__(
        self,
        repo: NotificationRepository,
        client: TelegramClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        poll_interval_seconds: float = 5.0,
        chat_budget: int = 15,
        budget_window_seconds: int = 60,
    ) -> None:
        self._repo = repo
        self._client = client
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._budget_window_seconds = budget_window_seconds
        self._budget = _ChatBudget(chat_budget, budget_window_seconds)
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def wake(self) -> None:
        """Wake up the dispatch loop immediately if waiting on poll interval."""
        self._wake_event.set()

    async def run_once(self) -> None:
        now = self._clock()
        due_actions = self._repo.list_due_actions(now)
        if not due_actions:
            return

        # Group actions by chat_id: preserve in-chat FIFO/priority ordering,
        # but process across different chats concurrently so one user's network
        # latency never delays another user.
        by_chat: dict[int, list[OutboxRecord]] = defaultdict(list)
        for action in due_actions:
            by_chat[action.chat_id].append(action)

        async def deliver_for_chat(actions: list[OutboxRecord]) -> None:
            for action in actions:
                if not self._budget.take(action.chat_id, now):
                    # Defer rather than burn a 429; the queue keeps its priority order.
                    self._repo.reschedule_action(
                        action.outbox_id,
                        now + timedelta(seconds=self._budget_window_seconds),
                        "Sohbet gönderim bütçesi doldu",
                        # Waiting its turn is not a failed attempt.
                        count_attempt=False,
                    )
                    continue
                try:
                    await self._deliver(action, now)
                except Exception as error:  # noqa: BLE001 - one action's failure must not
                    # stop the rest of this batch from being processed. Normal
                    # Telegram delivery failures are already returned as
                    # `TelegramResult` (never raised) and handled inside
                    # `_deliver`; this guards against anything unexpected
                    # (a bug, a repository error) so one user's durable state
                    # can never disable or delay another's (spec section 11).
                    logger.error(
                        "Delivery failed unexpectedly for outbox_id=%s user_id=%s: %s",
                        action.outbox_id,
                        action.user_id,
                        error,
                    )

        await asyncio.gather(*(deliver_for_chat(actions) for actions in by_chat.values()))

    async def _deliver(self, action: OutboxRecord, now: datetime) -> None:
        if action.action in _SEND_ACTIONS:
            reply_to = (
                action.message_id if action.action is not OutboxActionType.SEND_CARD else None
            )
            result = await self._client.send_text(
                chat_id=action.chat_id,
                text=action.text,
                reply_to_message_id=reply_to,
                silent=action.silent,
                reply_markup=action.reply_markup,
            )
        else:
            message_id = action.message_id
            if message_id is None and action.card_key:
                message_id = self._repo.get_card_message_id(action.user_id, action.card_key)
            if message_id is None:
                result = await self._client.send_text(
                    chat_id=action.chat_id,
                    text=action.text,
                    reply_markup=action.reply_markup,
                )
            else:
                result = await self._client.edit_text(
                    chat_id=action.chat_id,
                    message_id=message_id,
                    text=action.text,
                    reply_markup=action.reply_markup,
                )

        if result.ok:
            self._repo.mark_action_delivered(action.outbox_id, now, message_id=result.message_id)
            self._repo.record_delivery_success(action.user_id, now)
            return

        error = result.safe_error or "Telegram delivery failed"

        if result.failure_kind is TelegramFailureKind.CHAT_UNREACHABLE:
            self._repo.disable_for_user(action.user_id, error)
            return

        if result.failure_kind is TelegramFailureKind.MESSAGE_NOT_FOUND:
            replacement = OutboxActionDraft(
                action=OutboxActionType.SEND_CARD,
                text=action.text,
                fingerprint=action.fingerprint,
                next_attempt_at=now,
            )
            self._repo.replace_missing_message(action.outbox_id, replacement)
            self._repo.record_delivery_error(action.user_id, error)
            return

        if result.failure_kind is TelegramFailureKind.PERMANENT:
            self._repo.fail_action(action.outbox_id, error)
            self._repo.record_delivery_error(action.user_id, error)
            return

        if action.attempt_count + 1 >= MAX_ATTEMPTS:
            self._repo.fail_action(action.outbox_id, error)
            self._repo.record_delivery_error(action.user_id, error)
            return

        delay = self._backoff_seconds(action, result)
        self._repo.reschedule_action(action.outbox_id, now + timedelta(seconds=delay), error)
        self._repo.record_delivery_error(action.user_id, error)

    def _backoff_seconds(self, action: OutboxRecord, result: TelegramResult) -> int:
        if result.failure_kind is TelegramFailureKind.RATE_LIMITED:
            requested = result.retry_after_seconds or 1
            return min(max(requested, 1), _MAX_RATE_LIMIT_SECONDS)
        return min(_MAX_BACKOFF_SECONDS, int(2 ** min(action.attempt_count, 8)))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as error:  # noqa: BLE001 - dispatcher failures must not kill the loop
                logger.warning("Telegram dispatch cycle failed: %s", error)

            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._poll_interval_seconds)
                self._wake_event.clear()
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break

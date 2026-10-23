"""Telegram notification settings repository (token-free), scoped per user.

Ownership of every Telegram row is `user_id` -- never `chat_id`. Two accounts
may configure the same Chat ID; `chat_id` is retained purely as delivery
metadata (which Telegram chat to send/edit against), not as a lookup or
uniqueness key. See `_coalesce_key` for how outbox coalescing keeps two
users' actions on the same chat from colliding.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from surebet.notifications.models import (
    CardState,
    OutboxActionDraft,
    OutboxActionType,
    OutboxRecord,
)

from .db import get_connection

_MAX_CHAT_ID = 9_223_372_036_854_775_807


def _dump_card_state(state: CardState) -> str:
    return state.model_dump_json(
        warnings="none",
        fallback=lambda v: dict(v) if isinstance(v, (MappingProxyType, Mapping)) else str(v),
    )


class TelegramNotificationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    enabled: bool = False
    chat_id: int | None = Field(default=None, gt=0, le=_MAX_CHAT_ID)
    minimum_profit_bps: int = Field(default=95, ge=1, le=10_000)
    decrease_threshold_bps: int = Field(default=20, ge=1, le=10_000)
    alert_cooldown_seconds: int = Field(default=300, ge=60, le=86_400)
    increase_threshold_bps: int = Field(default=20, ge=1, le=10_000)
    validated_chat_id: int | None = None
    validated_at: datetime | None = None
    last_successful_delivery_at: datetime | None = None
    last_error: str | None = None
    notify_1x2: bool = True
    notify_ou25: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TelegramSettingsUpdate(BaseModel):
    enabled: bool | None = None
    chat_id: int | None = Field(default=None, gt=0, le=_MAX_CHAT_ID)
    minimum_profit_bps: int | None = Field(default=None, ge=1, le=10_000)
    decrease_threshold_bps: int | None = Field(default=None, ge=1, le=10_000)
    alert_cooldown_seconds: int | None = Field(default=None, ge=60, le=86_400)
    increase_threshold_bps: int | None = Field(default=None, ge=1, le=10_000)
    notify_1x2: bool | None = None
    notify_ou25: bool | None = None


_SETTINGS_COLUMNS = (
    "user_id, enabled, chat_id, minimum_profit_bps, decrease_threshold_bps, "
    "alert_cooldown_seconds, increase_threshold_bps, validated_chat_id, validated_at, "
    "last_successful_delivery_at, last_error, notify_1x2, notify_ou25, updated_at"
)


class NotificationRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path

    def get_settings(self, user_id: int) -> TelegramNotificationSettings:
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                f"SELECT {_SETTINGS_COLUMNS} "
                "FROM telegram_notification_settings WHERE user_id = ?;",
                (user_id,),
            ).fetchone()

            if row is None:
                defaults = TelegramNotificationSettings(user_id=user_id)
                conn.execute(
                    "INSERT INTO telegram_notification_settings "
                    "(user_id, enabled, chat_id, minimum_profit_bps, decrease_threshold_bps, "
                    "alert_cooldown_seconds, increase_threshold_bps, validated_chat_id, validated_at, "
                    "last_successful_delivery_at, last_error, notify_1x2, notify_ou25, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        user_id,
                        int(defaults.enabled),
                        defaults.chat_id,
                        defaults.minimum_profit_bps,
                        defaults.decrease_threshold_bps,
                        defaults.alert_cooldown_seconds,
                        defaults.increase_threshold_bps,
                        defaults.validated_chat_id,
                        None,
                        None,
                        None,
                        int(defaults.notify_1x2),
                        int(defaults.notify_ou25),
                        defaults.updated_at.isoformat(),
                    ),
                )
                return defaults

            return _settings_from_row(row)

    def list_enabled_settings(self) -> tuple[TelegramNotificationSettings, ...]:
        """All users whose Telegram settings are enabled and have a validated chat.

        A row is "deliverable" the same way the pre-multi-user dispatch code
        decided readiness: `enabled` is set, a `chat_id` is configured, and
        that exact chat has been validated (`chat_id == validated_chat_id`;
        changing the chat clears `validated_chat_id`, so a stale validation
        from a prior chat can never satisfy this).
        """
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {_SETTINGS_COLUMNS} "
                "FROM telegram_notification_settings "
                "WHERE enabled = 1 AND chat_id IS NOT NULL "
                "AND validated_chat_id IS NOT NULL AND chat_id = validated_chat_id;"
            ).fetchall()
        return tuple(_settings_from_row(row) for row in rows)

    def update_settings(
        self, user_id: int, updates: TelegramSettingsUpdate
    ) -> TelegramNotificationSettings:
        current = self.get_settings(user_id)
        fields = updates.model_fields_set
        new_enabled = current.enabled if updates.enabled is None else updates.enabled
        new_chat_id = updates.chat_id if "chat_id" in fields else current.chat_id
        new_minimum = (
            current.minimum_profit_bps
            if updates.minimum_profit_bps is None
            else updates.minimum_profit_bps
        )
        new_decrease = (
            current.decrease_threshold_bps
            if updates.decrease_threshold_bps is None
            else updates.decrease_threshold_bps
        )
        new_cooldown = (
            current.alert_cooldown_seconds
            if updates.alert_cooldown_seconds is None
            else updates.alert_cooldown_seconds
        )
        new_increase = (
            current.increase_threshold_bps
            if updates.increase_threshold_bps is None
            else updates.increase_threshold_bps
        )
        new_notify_1x2 = current.notify_1x2 if updates.notify_1x2 is None else updates.notify_1x2
        new_notify_ou25 = (
            current.notify_ou25 if updates.notify_ou25 is None else updates.notify_ou25
        )

        chat_changed = "chat_id" in fields and new_chat_id != current.chat_id
        new_validated_chat_id = None if chat_changed else current.validated_chat_id
        new_validated_at = None if chat_changed else current.validated_at
        now = datetime.now(UTC)

        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO telegram_notification_settings "
                "(user_id, enabled, chat_id, minimum_profit_bps, decrease_threshold_bps, "
                "alert_cooldown_seconds, increase_threshold_bps, validated_chat_id, validated_at, "
                "last_successful_delivery_at, last_error, notify_1x2, notify_ou25, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "enabled = excluded.enabled, "
                "chat_id = excluded.chat_id, "
                "minimum_profit_bps = excluded.minimum_profit_bps, "
                "decrease_threshold_bps = excluded.decrease_threshold_bps, "
                "alert_cooldown_seconds = excluded.alert_cooldown_seconds, "
                "increase_threshold_bps = excluded.increase_threshold_bps, "
                "validated_chat_id = excluded.validated_chat_id, "
                "validated_at = excluded.validated_at, "
                "notify_1x2 = excluded.notify_1x2, "
                "notify_ou25 = excluded.notify_ou25, "
                "updated_at = excluded.updated_at;",
                (
                    user_id,
                    int(new_enabled),
                    new_chat_id,
                    new_minimum,
                    new_decrease,
                    new_cooldown,
                    new_increase,
                    new_validated_chat_id,
                    new_validated_at.isoformat() if new_validated_at else None,
                    current.last_successful_delivery_at.isoformat()
                    if current.last_successful_delivery_at
                    else None,
                    current.last_error,
                    int(new_notify_1x2),
                    int(new_notify_ou25),
                    now.isoformat(),
                ),
            )

            if current.enabled and not new_enabled:
                conn.execute(
                    "DELETE FROM telegram_notification_outbox "
                    "WHERE user_id = ? AND status = 'pending';",
                    (user_id,),
                )

            if not new_notify_1x2:
                conn.execute(
                    "DELETE FROM telegram_notification_outbox "
                    "WHERE user_id = ? AND status = 'pending' AND card_key LIKE '%:1X2';",
                    (user_id,),
                )

            if not new_notify_ou25:
                conn.execute(
                    "DELETE FROM telegram_notification_outbox "
                    "WHERE user_id = ? AND status = 'pending' AND card_key LIKE '%:OU25';",
                    (user_id,),
                )

        return self.get_settings(user_id)

    def mark_chat_validated(
        self, user_id: int, chat_id: int, validated_at: datetime
    ) -> TelegramNotificationSettings:
        self.get_settings(user_id)
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_settings "
                "SET validated_chat_id = ?, validated_at = ?, updated_at = ? "
                "WHERE user_id = ?;",
                (chat_id, validated_at.isoformat(), datetime.now(UTC).isoformat(), user_id),
            )
        return self.get_settings(user_id)

    def get_user_id_by_chat_id(self, chat_id: int) -> int | None:
        """Find user_id for a given chat_id."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT user_id FROM telegram_notification_settings "
                "WHERE validated_chat_id = ? OR chat_id = ? "
                "ORDER BY (validated_chat_id = ?) DESC LIMIT 1;",
                (chat_id, chat_id, chat_id),
            ).fetchone()
            if row:
                return int(row["user_id"])
            return None

    def record_delivery_success(
        self, user_id: int, delivered_at: datetime
    ) -> TelegramNotificationSettings:
        self.get_settings(user_id)
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_settings "
                "SET last_successful_delivery_at = ?, last_error = NULL, updated_at = ? "
                "WHERE user_id = ?;",
                (delivered_at.isoformat(), datetime.now(UTC).isoformat(), user_id),
            )
        return self.get_settings(user_id)

    def record_delivery_error(self, user_id: int, error: str) -> TelegramNotificationSettings:
        self.get_settings(user_id)
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_settings "
                "SET last_error = ?, updated_at = ? WHERE user_id = ?;",
                (error, datetime.now(UTC).isoformat(), user_id),
            )
        return self.get_settings(user_id)

    def list_current_cards(
        self, user_id: int, *, retired_before: datetime | None = None
    ) -> dict[str, CardState]:
        """Newest generation of every card for one user.

        `retired_before` narrows the read to cards that can still act: a card
        closed before that instant is past its revival window, so it can neither
        revive nor produce an action, and re-reading it every cycle would make
        the per-cycle reconcile grow monotonically with the user's whole card
        history (spec section 7.6).
        """
        params: list[object] = [user_id]
        scope = ""
        if retired_before is not None:
            # `closed_at` (the logical close instant) rather than `updated_at`
            # (the wall-clock write time), so the scope follows the card's own
            # timeline. The sentinel keeps a NULL `closed_at` on the safe side.
            scope = (
                " AND NOT (status = 'closed' AND "
                "COALESCE(json_extract(state_json, '$.closed_at'), '9999') < ?)"
            )
            params.append(retired_before.isoformat())
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT state_json FROM ("
                "  SELECT state_json, ROW_NUMBER() OVER ("
                "    PARTITION BY card_key ORDER BY generation DESC"
                f"  ) AS rank FROM telegram_opportunity_cards WHERE user_id = ?{scope}"
                ") WHERE rank = 1;",
                tuple(params),
            ).fetchall()
        states = (CardState.model_validate_json(row["state_json"]) for row in rows)
        return {state.card_key: state for state in states}

    def get_card_message_id(self, user_id: int, card_key: str) -> int | None:
        """Fetch the message id of the latest generation of a card without full deserialization."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT message_id FROM telegram_opportunity_cards "
                "WHERE user_id = ? AND card_key = ? "
                "ORDER BY generation DESC LIMIT 1;",
                (user_id, card_key),
            ).fetchone()
            if row is not None and row["message_id"] is not None:
                return int(row["message_id"])
            return None

    def get_card_by_message_id(self, chat_id: int, message_id: int) -> CardState | None:
        """Find a card state by its Telegram chat_id and message_id."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM telegram_opportunity_cards "
                "WHERE chat_id = ? AND message_id = ? "
                "ORDER BY generation DESC LIMIT 1;",
                (chat_id, message_id),
            ).fetchone()
            if row is not None:
                return CardState.model_validate_json(row["state_json"])
            return None

    def get_card_by_key(self, card_key: str) -> CardState | None:
        """Find the latest card state by its card_key."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM telegram_opportunity_cards "
                "WHERE card_key = ? "
                "ORDER BY generation DESC LIMIT 1;",
                (card_key,),
            ).fetchone()
            if row is not None:
                return CardState.model_validate_json(row["state_json"])
            return None

    def get_latest_card_for_chat(self, chat_id: int) -> CardState | None:
        """Find the most recently updated card for a chat."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM telegram_opportunity_cards "
                "WHERE chat_id = ? "
                "ORDER BY updated_at DESC LIMIT 1;",
                (chat_id,),
            ).fetchone()
            if row is not None:
                return CardState.model_validate_json(row["state_json"])
            return None

    def mute_card(
        self,
        chat_id: int,
        card_key: str,
        duration_seconds: int = 7200,
        now: datetime | None = None,
    ) -> datetime:
        """Mute updates for a card for the specified duration (default 2 hours)."""
        current_time = now or datetime.now(UTC)
        muted_until = current_time + timedelta(seconds=duration_seconds)
        now_iso = current_time.isoformat()
        until_iso = muted_until.isoformat()
        with get_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO telegram_muted_cards (chat_id, card_key, muted_until, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, card_key) DO UPDATE SET "
                "muted_until = excluded.muted_until;",
                (chat_id, card_key, until_iso, now_iso),
            )
        return muted_until

    def unmute_card(self, chat_id: int, card_key: str) -> None:
        """Unmute a previously muted card."""
        with get_connection(self._db_path) as conn:
            conn.execute(
                "DELETE FROM telegram_muted_cards WHERE chat_id = ? AND card_key = ?;",
                (chat_id, card_key),
            )

    def is_card_muted(self, chat_id: int, card_key: str, now: datetime | None = None) -> bool:
        """Check if a card is currently muted."""
        ref_time = (now or datetime.now(UTC)).isoformat()
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM telegram_muted_cards "
                "WHERE chat_id = ? AND card_key = ? AND muted_until > ?;",
                (chat_id, card_key, ref_time),
            ).fetchone()
            return row is not None

    def toggle_favorite(self, chat_id: int, card_key: str) -> bool:
        """Toggle favorite status of a card for a chat. Returns True if now favorited."""
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM telegram_favorite_cards WHERE chat_id = ? AND card_key = ?;",
                (chat_id, card_key),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "DELETE FROM telegram_favorite_cards WHERE chat_id = ? AND card_key = ?;",
                    (chat_id, card_key),
                )
                return False
            conn.execute(
                "INSERT INTO telegram_favorite_cards (chat_id, card_key, created_at) "
                "VALUES (?, ?, ?);",
                (chat_id, card_key, now_iso),
            )
            return True

    def is_favorite(self, chat_id: int, card_key: str) -> bool:
        """Check if a card is currently favorited."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM telegram_favorite_cards WHERE chat_id = ? AND card_key = ?;",
                (chat_id, card_key),
            ).fetchone()
            return row is not None

    def list_favorites(self, chat_id: int) -> tuple[str, ...]:
        """List all card_keys currently favorited by a chat."""
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT card_key FROM telegram_favorite_cards WHERE chat_id = ? ORDER BY created_at DESC;",
                (chat_id,),
            ).fetchall()
            return tuple(row["card_key"] for row in rows)

    def purge_retired_cards(self, before: datetime) -> int:
        """Delete closed cards last touched before `before`. Returns the row count."""
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM telegram_opportunity_cards "
                "WHERE status = 'closed' AND updated_at < ?;",
                (before.isoformat(),),
            )
            return cursor.rowcount

    def purge_completed_outbox(self, before: datetime) -> int:
        """Delete delivered/superseded outbox records updated before `before`.

        Pending and failed rows are preserved for inspection and retry.
        Returns the deleted row count.
        """
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM telegram_notification_outbox "
                "WHERE status IN ('delivered', 'superseded') AND updated_at < ?;",
                (before.isoformat(),),
            )
            return cursor.rowcount

    def apply_transitions(
        self,
        transitions: Sequence[tuple[CardState, tuple[OutboxActionDraft, ...]]],
    ) -> None:
        """Persist every card and its drafts for one user in a single transaction."""
        if not transitions:
            return
        with get_connection(self._db_path) as conn:
            for state, drafts in transitions:
                card_updated_iso = (
                    state.last_seen_at or state.closed_at or datetime.now(UTC)
                ).isoformat()
                card_created_iso = (
                    state.first_seen_at or state.last_seen_at or datetime.now(UTC)
                ).isoformat()
                conn.execute(
                    "INSERT INTO telegram_opportunity_cards "
                    "(user_id, chat_id, card_key, matched_id, market_type, generation, status, "
                    "message_id, state_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id, card_key, generation) DO UPDATE SET "
                    "status = excluded.status, message_id = excluded.message_id, "
                    "state_json = excluded.state_json, updated_at = excluded.updated_at;",
                    (
                        state.user_id,
                        state.chat_id,
                        state.card_key,
                        state.matched_id,
                        state.market_type.value,
                        state.generation,
                        state.status.value,
                        state.message_id,
                        _dump_card_state(state),
                        card_created_iso,
                        card_updated_iso,
                    ),
                )
                for draft in drafts:
                    draft_iso = (draft.next_attempt_at or datetime.now(UTC)).isoformat()
                    conn.execute(
                        "INSERT INTO telegram_notification_outbox "
                        "(user_id, chat_id, card_key, matched_id, generation, action, "
                        "coalesce_key, message_id, silent, priority, payload_json, status, "
                        "attempt_count, next_attempt_at, last_error, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?, ?) "
                        "ON CONFLICT(coalesce_key) DO UPDATE SET "
                        "action = CASE "
                        "    WHEN telegram_notification_outbox.action = 'send_card' THEN telegram_notification_outbox.action "
                        "    ELSE excluded.action "
                        "END, "
                        "payload_json = excluded.payload_json, "
                        "message_id = excluded.message_id, silent = excluded.silent, "
                        "priority = MIN(telegram_notification_outbox.priority, excluded.priority), "
                        "status = 'pending', "
                        "next_attempt_at = excluded.next_attempt_at, "
                        # attempt_count is deliberately NOT reset: a card whose
                        # text changes once per retry window would otherwise
                        # restart its retry budget every cycle and never reach
                        # MAX_ATTEMPTS (spec section 7.4).
                        "last_error = NULL, updated_at = excluded.updated_at;",
                        (
                            state.user_id,
                            state.chat_id,
                            state.card_key,
                            state.matched_id,
                            state.generation,
                            draft.action.value,
                            _coalesce_key(state, draft),
                            draft.message_id,
                            int(draft.silent),
                            _PRIORITY[draft.action],
                            draft.model_dump_json(),
                            draft.next_attempt_at.isoformat(),
                            draft_iso,
                            draft_iso,
                        ),
                    )

    def fail_action(self, outbox_id: int, error: str) -> None:
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_outbox SET status = 'failed', "
                "coalesce_key = NULL, last_error = ?, updated_at = ? WHERE outbox_id = ?;",
                (error, datetime.now(UTC).isoformat(), outbox_id),
            )

    def disable_for_user(self, user_id: int, error: str) -> None:
        """Stop delivering for a chat we can no longer reach, instead of retrying forever."""
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_settings SET enabled = 0, last_error = ?, "
                "updated_at = ? WHERE user_id = ?;",
                (error, datetime.now(UTC).isoformat(), user_id),
            )
            conn.execute(
                "DELETE FROM telegram_notification_outbox "
                "WHERE user_id = ? AND status = 'pending';",
                (user_id,),
            )

    def list_due_actions(self, now: datetime, limit: int = 50) -> tuple[OutboxRecord, ...]:
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT o.outbox_id, o.user_id, o.chat_id, o.card_key, o.matched_id, o.generation, o.action, "
                "o.message_id, o.silent, o.payload_json, o.attempt_count, o.next_attempt_at "
                "FROM telegram_notification_outbox o "
                "LEFT JOIN telegram_notification_settings s ON o.user_id = s.user_id "
                "WHERE o.status = 'pending' AND o.next_attempt_at <= ? "
                "AND (s.notify_1x2 IS NULL OR s.notify_1x2 = 1 OR o.card_key NOT LIKE '%:1X2') "
                "AND (s.notify_ou25 IS NULL OR s.notify_ou25 = 1 OR o.card_key NOT LIKE '%:OU25') "
                "ORDER BY o.priority ASC, o.next_attempt_at ASC, o.outbox_id ASC LIMIT ?;",
                (now.isoformat(), limit),
            ).fetchall()
        records: list[OutboxRecord] = []
        for row in rows:
            draft = OutboxActionDraft.model_validate_json(row["payload_json"])
            records.append(
                OutboxRecord(
                    outbox_id=row["outbox_id"],
                    user_id=row["user_id"],
                    chat_id=row["chat_id"],
                    card_key=row["card_key"] or "",
                    matched_id=row["matched_id"],
                    generation=row["generation"],
                    action=OutboxActionType(row["action"]),
                    text=draft.text,
                    fingerprint=draft.fingerprint,
                    message_id=row["message_id"],
                    silent=bool(row["silent"]),
                    attempt_count=row["attempt_count"],
                    next_attempt_at=datetime.fromisoformat(row["next_attempt_at"]),
                    reply_markup=draft.reply_markup,
                )
            )
        return tuple(records)

    def mark_action_delivered(
        self, outbox_id: int, delivered_at: datetime, message_id: int | None = None
    ) -> None:
        """Mark one outbox row delivered, optionally recording its message id.

        `outbox_id` is an AUTOINCREMENT primary key, so looking a row up by
        it alone is already unambiguous -- no caller can hold another user's
        `outbox_id` without already having read it from their own
        user-scoped `list_due_actions`/`apply_transitions` call. The follow-up
        update to `telegram_opportunity_cards`, however, joins on
        `(card_key, generation)`, which are NOT globally
        unique (two users can legitimately share both, e.g. tracking the same real-world event
        at the same generation) -- so that predicate must also include the
        owning `user_id`, read from the outbox row itself, to avoid updating
        a different user's state that happens to match on chat/matched/gen.
        """
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT user_id, chat_id, card_key, matched_id, generation, action "
                "FROM telegram_notification_outbox WHERE outbox_id = ?;",
                (outbox_id,),
            ).fetchone()
            conn.execute(
                "UPDATE telegram_notification_outbox SET status = 'delivered', "
                "coalesce_key = NULL, updated_at = ? WHERE outbox_id = ?;",
                (delivered_at.isoformat(), outbox_id),
            )
            # A row without a `card_key` (the one-shot sunset edits written by
            # `_migrate_telegram_cards`) has no card to stamp -- marking the
            # outbox row delivered is the whole of its bookkeeping.
            if row is not None and row["card_key"]:
                card_row = conn.execute(
                    "SELECT state_json FROM telegram_opportunity_cards "
                    "WHERE user_id = ? AND card_key = ? AND generation = ?;",
                    (row["user_id"], row["card_key"], row["generation"]),
                ).fetchone()
                if card_row is not None:
                    card_state = CardState.model_validate_json(card_row["state_json"])
                    updates: dict[str, Any] = {}
                    action = row["action"]
                    # Only the card-bearing actions carry the card's own
                    # message id. `SEND_ALERT` / `SEND_CLOSED` / `SEND_REVIVED`
                    # create a REPLY, and stamping the reply's id onto the card
                    # would make the next `EDIT_CARD` rewrite the reply.
                    card_bearing = action in (
                        OutboxActionType.SEND_CARD,
                        OutboxActionType.EDIT_CARD,
                        "send_card",
                        "edit_card",
                    )
                    if card_bearing:
                        if message_id is not None:
                            updates["message_id"] = message_id
                        updates["last_edit_at"] = delivered_at
                    if updates:
                        updated_card = card_state.model_copy(update=updates)
                        conn.execute(
                            "UPDATE telegram_opportunity_cards "
                            "SET message_id = ?, state_json = ?, updated_at = ? "
                            "WHERE user_id = ? AND card_key = ? AND generation = ?;",
                            (
                                updated_card.message_id,
                                _dump_card_state(updated_card),
                                delivered_at.isoformat(),
                                row["user_id"],
                                row["card_key"],
                                row["generation"],
                            ),
                        )

    def reschedule_action(
        self,
        outbox_id: int,
        next_attempt_at: datetime,
        error: str,
        *,
        count_attempt: bool = True,
    ) -> None:
        """Push one pending row into the future.

        `count_attempt=False` is for rows that were never actually attempted --
        a chat-budget deferral only waits its turn, so charging it a retry would
        inflate the backoff of a perfectly healthy message and dead-letter it
        once a genuine transient failure finally arrives.
        """
        # Scoped by outbox_id alone: this only touches columns local to the
        # single outbox row already identified by its AUTOINCREMENT primary
        # key, so no cross-user join is possible here.
        attempt_clause = "attempt_count = attempt_count + 1, " if count_attempt else ""
        with get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE telegram_notification_outbox SET next_attempt_at = ?, last_error = ?, "
                f"{attempt_clause}updated_at = ? WHERE outbox_id = ?;",
                (next_attempt_at.isoformat(), error, datetime.now(UTC).isoformat(), outbox_id),
            )

    def replace_missing_message(self, outbox_id: int, replacement: OutboxActionDraft) -> None:
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT user_id, chat_id, card_key, matched_id, generation "
                "FROM telegram_notification_outbox WHERE outbox_id = ?;",
                (outbox_id,),
            ).fetchone()
            conn.execute(
                "UPDATE telegram_notification_outbox SET status = 'superseded', updated_at = ? "
                "WHERE outbox_id = ?;",
                (now_iso, outbox_id),
            )
            if row is not None and row["card_key"]:
                # The message we were editing is gone for good. Forget its id,
                # or the next cycle drafts another `EDIT_CARD` at the same dead
                # target, coalesces over this replacement, and fails forever.
                self._forget_card_message_id(conn, row, now_iso)
            if row is not None:
                coalesce_key = (
                    f"card:{row['user_id']}:{row['chat_id']}:{row['card_key']}:{row['generation']}"
                )
                conn.execute(
                    "INSERT INTO telegram_notification_outbox "
                    "(user_id, chat_id, card_key, matched_id, generation, action, coalesce_key, "
                    "payload_json, status, attempt_count, next_attempt_at, last_error, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?, ?) "
                    "ON CONFLICT(coalesce_key) DO UPDATE SET "
                    "action = excluded.action, "
                    "payload_json = excluded.payload_json, "
                    "status = 'pending', "
                    "next_attempt_at = excluded.next_attempt_at, "
                    "last_error = NULL, "
                    "updated_at = excluded.updated_at;",
                    (
                        row["user_id"],
                        row["chat_id"],
                        row["card_key"],
                        row["matched_id"],
                        row["generation"],
                        OutboxActionType.SEND_CARD.value,
                        coalesce_key,
                        replacement.model_dump_json(),
                        replacement.next_attempt_at.isoformat(),
                        now_iso,
                        now_iso,
                    ),
                )

    @staticmethod
    def _forget_card_message_id(conn: Any, row: Any, now_iso: str) -> None:
        card_row = conn.execute(
            "SELECT state_json FROM telegram_opportunity_cards "
            "WHERE user_id = ? AND card_key = ? AND generation = ?;",
            (row["user_id"], row["card_key"], row["generation"]),
        ).fetchone()
        if card_row is None:
            return
        cleared = CardState.model_validate_json(card_row["state_json"]).model_copy(
            update={"message_id": None}
        )
        conn.execute(
            "UPDATE telegram_opportunity_cards "
            "SET message_id = NULL, state_json = ?, updated_at = ? "
            "WHERE user_id = ? AND card_key = ? AND generation = ?;",
            (
                _dump_card_state(cleared),
                now_iso,
                row["user_id"],
                row["card_key"],
                row["generation"],
            ),
        )


_PRIORITY = {
    OutboxActionType.SEND_CARD: 0,
    OutboxActionType.SEND_CLOSED: 0,
    OutboxActionType.SEND_ALERT: 1,
    OutboxActionType.SEND_REVIVED: 1,
    OutboxActionType.EDIT_CARD: 2,
}


def _coalesce_key(state: CardState, draft: OutboxActionDraft) -> str:
    # user_id comes first so that two users configuring the same chat_id
    # (deliberately supported -- see the module docstring) never produce the
    # same coalesce_key, and therefore never collide on the outbox table's
    # UNIQUE(coalesce_key) constraint.
    base = f"{state.user_id}:{state.chat_id}:{state.card_key}:{state.generation}"
    if draft.action in (OutboxActionType.SEND_CARD, OutboxActionType.EDIT_CARD):
        return f"card:{base}"
    if draft.action is OutboxActionType.SEND_ALERT:
        marker = state.last_alert_at.isoformat() if state.last_alert_at else "na"
        return f"alert:{base}:{marker}"
    if draft.action is OutboxActionType.SEND_REVIVED:
        return f"revived:{base}:{state.revival_count}"
    return f"closed:{base}"


def _settings_from_row(row: sqlite3.Row) -> TelegramNotificationSettings:
    return TelegramNotificationSettings(
        user_id=row["user_id"],
        enabled=bool(row["enabled"]),
        chat_id=row["chat_id"],
        minimum_profit_bps=row["minimum_profit_bps"],
        decrease_threshold_bps=row["decrease_threshold_bps"],
        alert_cooldown_seconds=row["alert_cooldown_seconds"],
        increase_threshold_bps=row["increase_threshold_bps"],
        validated_chat_id=row["validated_chat_id"],
        validated_at=(datetime.fromisoformat(row["validated_at"]) if row["validated_at"] else None),
        last_successful_delivery_at=(
            datetime.fromisoformat(row["last_successful_delivery_at"])
            if row["last_successful_delivery_at"]
            else None
        ),
        last_error=row["last_error"],
        notify_1x2=bool(row["notify_1x2"]),
        notify_ou25=bool(row["notify_ou25"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )

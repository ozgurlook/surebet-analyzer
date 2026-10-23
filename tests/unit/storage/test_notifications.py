"""Per-user isolation and atomicity tests for the Telegram persistence layer.

`telegram_notification_settings`, `telegram_opportunity_cards`, and
`telegram_notification_outbox` all key ownership by `user_id` -- never by
`chat_id`, which is retained purely as delivery metadata. Two accounts may
configure the same Chat ID without sharing lifecycle state, outbox rows, or
message IDs; these tests prove exactly that, plus that deleting a user
cascades only that user's Telegram data.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from surebet.notifications.models import (
    CardState,
    LifecycleStatus,
    OutboxActionDraft,
    OutboxActionType,
)
from surebet.storage.auth import AuthRepository
from surebet.storage.db import get_connection, init_db
from surebet.storage.notifications import (
    NotificationRepository,
    TelegramSettingsUpdate,
    _coalesce_key,
)

FAKE_PASSWORD = "correct-horse-battery-staple"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
CHAT_ID = 123
MATCHED_ID = "event-1"
KICKOFF = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
_DB_FILENAME = "notifications.db"


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / _DB_FILENAME


def _make_two_users(db_path: Path) -> tuple[int, int]:
    auth = AuthRepository(db_path)
    user_a, _ = auth.register_user("alice", FAKE_PASSWORD)
    user_b, _ = auth.register_user("bob", FAKE_PASSWORD)
    return user_a.user_id, user_b.user_id


def initialized_repo(tmp_path: Path) -> tuple[NotificationRepository, int, int]:
    db_path = _db_path(tmp_path)
    init_db(db_path)
    user_a, user_b = _make_two_users(db_path)
    return NotificationRepository(db_path), user_a, user_b


# -- settings are keyed by user_id -------------------------------------------


def test_notification_settings_default_and_are_keyed_by_user(tmp_path: Path) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)

    settings_a = repo.get_settings(user_a)

    assert settings_a.user_id == user_a
    assert settings_a.enabled is False
    assert settings_a.chat_id is None
    assert settings_a.minimum_profit_bps == 95
    assert settings_a.decrease_threshold_bps == 20
    assert settings_a.alert_cooldown_seconds == 300
    assert settings_a.increase_threshold_bps == 20
    assert settings_a.validated_chat_id is None
    assert settings_a.notify_1x2 is True
    assert settings_a.notify_ou25 is True


def test_updating_market_notification_settings(tmp_path: Path) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)

    updated_a = repo.update_settings(
        user_a,
        TelegramSettingsUpdate(notify_1x2=False, notify_ou25=True),
    )
    assert updated_a.notify_1x2 is False
    assert updated_a.notify_ou25 is True

    settings_b = repo.get_settings(user_b)
    assert settings_b.notify_1x2 is True
    assert settings_b.notify_ou25 is True


def test_updating_one_users_settings_does_not_affect_the_other(tmp_path: Path) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)

    updated_a = repo.update_settings(
        user_a,
        TelegramSettingsUpdate(
            enabled=True,
            chat_id=CHAT_ID,
            minimum_profit_bps=150,
            increase_threshold_bps=50,
        ),
    )

    assert updated_a.user_id == user_a
    assert updated_a.enabled is True
    assert updated_a.chat_id == CHAT_ID
    assert updated_a.minimum_profit_bps == 150
    assert updated_a.increase_threshold_bps == 50

    settings_b = repo.get_settings(user_b)
    assert settings_b.enabled is False
    assert settings_b.chat_id is None
    assert settings_b.minimum_profit_bps == 95
    assert settings_b.increase_threshold_bps == 20


def test_two_users_can_configure_the_same_chat_id_independently(tmp_path: Path) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)

    settings_a = repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    settings_b = repo.update_settings(user_b, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))

    assert settings_a.chat_id == settings_b.chat_id == CHAT_ID
    assert settings_a.user_id != settings_b.user_id


def test_changing_chat_invalidates_validation(tmp_path: Path) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=CHAT_ID))
    repo.mark_chat_validated(user_a, CHAT_ID, NOW)

    changed = repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=456))

    assert changed.chat_id == 456
    assert changed.validated_chat_id is None
    assert changed.validated_at is None


def test_updating_unrelated_field_preserves_existing_validation(tmp_path: Path) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=CHAT_ID))
    repo.mark_chat_validated(user_a, CHAT_ID, NOW)

    unchanged = repo.update_settings(user_a, TelegramSettingsUpdate(minimum_profit_bps=200))

    assert unchanged.chat_id == CHAT_ID
    assert unchanged.validated_chat_id == CHAT_ID
    assert unchanged.validated_at == NOW


def test_omitted_chat_id_does_not_clear_existing_chat(tmp_path: Path) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=CHAT_ID))

    result = repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True))

    assert result.chat_id == CHAT_ID


def test_explicit_null_chat_id_clears_existing_chat(tmp_path: Path) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=CHAT_ID))
    repo.mark_chat_validated(user_a, CHAT_ID, NOW)

    result = repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=None))

    assert result.chat_id is None
    assert result.validated_chat_id is None


def test_mark_chat_validated_only_touches_the_owning_users_settings(tmp_path: Path) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(chat_id=CHAT_ID))
    repo.update_settings(user_b, TelegramSettingsUpdate(chat_id=CHAT_ID))

    repo.mark_chat_validated(user_a, CHAT_ID, NOW)

    assert repo.get_settings(user_a).validated_chat_id == CHAT_ID
    assert repo.get_settings(user_b).validated_chat_id is None


def test_record_delivery_success_and_error_only_touch_the_owning_users_settings(
    tmp_path: Path,
) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)

    after_success = repo.record_delivery_success(user_a, NOW)
    assert after_success.last_successful_delivery_at == NOW
    assert after_success.last_error is None
    assert repo.get_settings(user_b).last_successful_delivery_at is None

    after_error = repo.record_delivery_error(user_a, "Telegram HTTP 400")
    assert after_error.last_error == "Telegram HTTP 400"
    assert after_error.last_successful_delivery_at == NOW
    assert repo.get_settings(user_b).last_error is None


def test_list_enabled_settings_returns_only_enabled_and_validated_rows(tmp_path: Path) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_a, CHAT_ID, NOW)

    # user_b is enabled but never validated its chat -- not deliverable.
    repo.update_settings(user_b, TelegramSettingsUpdate(enabled=True, chat_id=456))

    enabled = repo.list_enabled_settings()

    assert [settings.user_id for settings in enabled] == [user_a]
    assert enabled[0].chat_id == CHAT_ID


def test_list_enabled_settings_excludes_disabled_even_if_previously_validated(
    tmp_path: Path,
) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.mark_chat_validated(user_a, CHAT_ID, NOW)
    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=False))

    assert repo.list_enabled_settings() == ()


# -- lifecycle identity is (user_id, matched_id, generation) -----------------


def _card(notif, user_id: int, **overrides) -> CardState:
    return notif.card_state(user_id=user_id, chat_id=CHAT_ID, **overrides)


def _draft(action: OutboxActionType, *, profit_bps: int) -> OutboxActionDraft:
    return OutboxActionDraft(
        action=action,
        text=f"profit={profit_bps}",
        fingerprint=f"fp-{profit_bps}",
        next_attempt_at=NOW,
    )


def test_lifecycle_identity_is_user_card_generation_not_chat_id(tmp_path: Path, notif) -> None:
    """Two users sharing the same Chat ID must track fully independent lifecycles."""
    repo, user_a, user_b = initialized_repo(tmp_path)

    repo.apply_transitions([(_card(notif, user_a, current_profit_bps=134), ())])
    repo.apply_transitions([(_card(notif, user_b, current_profit_bps=90), ())])

    cards_a = repo.list_current_cards(user_a)
    cards_b = repo.list_current_cards(user_b)

    assert [card.current_profit_bps for card in cards_a.values()] == [134]
    assert [card.current_profit_bps for card in cards_b.values()] == [90]


def test_apply_transitions_persists_card_and_outbox_atomically(tmp_path: Path, notif) -> None:
    repo, user_a, _ = initialized_repo(tmp_path)
    card = _card(notif, user_a, current_profit_bps=134)
    repo.apply_transitions([(card, (_draft(OutboxActionType.SEND_CARD, profit_bps=134),))])

    assert repo.list_current_cards(user_a) == {card.card_key: card}
    due = repo.list_due_actions(NOW)
    assert [item.action for item in due] == [OutboxActionType.SEND_CARD]
    assert due[0].user_id == user_a


def test_restart_loads_current_generation_instead_of_creating_new(tmp_path: Path, notif) -> None:
    db_path = _db_path(tmp_path)
    init_db(db_path)
    user_a, _ = _make_two_users(db_path)
    NotificationRepository(db_path).apply_transitions([(_card(notif, user_a), ())])

    [current] = NotificationRepository(db_path).list_current_cards(user_a).values()

    assert current.generation == 1


# -- outbox coalescing includes user_id ---------------------------------------


def test_same_chat_card_action_for_two_users_creates_two_outbox_rows(tmp_path: Path, notif) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    send = _draft(OutboxActionType.SEND_CARD, profit_bps=134)

    repo.apply_transitions([(_card(notif, user_a), (send,))])
    repo.apply_transitions([(_card(notif, user_b), (send,))])

    due = repo.list_due_actions(NOW)
    assert len(due) == 2
    assert {record.user_id for record in due} == {user_a, user_b}


def test_newer_card_edit_replaces_pending_card_edit_only_for_the_owning_user(
    tmp_path: Path, notif
) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)

    def edit(profit_bps: int) -> OutboxActionDraft:
        return _draft(OutboxActionType.EDIT_CARD, profit_bps=profit_bps)

    repo.apply_transitions([(_card(notif, user_a), (edit(120),))])
    repo.apply_transitions([(_card(notif, user_a), (edit(110),))])
    repo.apply_transitions([(_card(notif, user_b), (edit(99),))])

    due = repo.list_due_actions(NOW)
    a_edits = [item for item in due if item.user_id == user_a]
    b_edits = [item for item in due if item.user_id == user_b]
    assert [item.text for item in a_edits] == ["profit=110"]
    assert [item.text for item in b_edits] == ["profit=99"]


# -- disabling / delivery scoping ----------------------------------------------


def test_disabling_cancels_pending_delivery_for_that_user_only(tmp_path: Path, notif) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.update_settings(user_b, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    send = _draft(OutboxActionType.SEND_CARD, profit_bps=134)
    repo.apply_transitions([(_card(notif, user_a), (send,))])
    repo.apply_transitions([(_card(notif, user_b), (send,))])

    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=False))

    due = repo.list_due_actions(NOW)
    assert [record.user_id for record in due] == [user_b]
    # The card itself is preserved; only its pending delivery was cancelled.
    assert repo.list_current_cards(user_a)
    assert repo.list_current_cards(user_b)


def test_mark_action_delivered_updates_message_id_only_for_the_owning_users_card(
    tmp_path: Path, notif
) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    edit = _draft(OutboxActionType.EDIT_CARD, profit_bps=134)
    repo.apply_transitions([(_card(notif, user_a), (edit,))])
    repo.apply_transitions([(_card(notif, user_b), (edit,))])

    due = repo.list_due_actions(NOW)
    [action_a] = [item for item in due if item.user_id == user_a]

    repo.mark_action_delivered(action_a.outbox_id, NOW, message_id=999)

    [updated_a] = repo.list_current_cards(user_a).values()
    [updated_b] = repo.list_current_cards(user_b).values()
    assert updated_a.message_id == 999
    assert updated_b.message_id is None


def test_replace_missing_message_new_row_belongs_to_the_owning_user(tmp_path: Path, notif) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    send = _draft(OutboxActionType.SEND_CARD, profit_bps=134)
    repo.apply_transitions([(_card(notif, user_a), (send,))])
    repo.apply_transitions([(_card(notif, user_b), (send,))])

    due = repo.list_due_actions(NOW)
    [action_a] = [item for item in due if item.user_id == user_a]
    repo.mark_action_delivered(action_a.outbox_id, NOW, message_id=555)

    repo.replace_missing_message(action_a.outbox_id, send)

    due_after = repo.list_due_actions(NOW)
    a_pending = [item for item in due_after if item.user_id == user_a]
    b_pending = [item for item in due_after if item.user_id == user_b]
    assert [item.action for item in a_pending] == [OutboxActionType.SEND_CARD]
    # user_b original pending SEND_CARD is untouched
    assert len(b_pending) == 1


# -- cascade deletion -----------------------------------------------------------


def test_cascade_deleting_a_user_removes_only_that_users_notification_data(
    tmp_path: Path, notif
) -> None:
    repo, user_a, user_b = initialized_repo(tmp_path)
    repo.update_settings(user_a, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    repo.update_settings(user_b, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))
    send = _draft(OutboxActionType.SEND_CARD, profit_bps=134)
    repo.apply_transitions([(_card(notif, user_a), (send,))])
    repo.apply_transitions([(_card(notif, user_b), (send,))])

    db_path = _db_path(tmp_path)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?;", (user_a,))

    with get_connection(db_path) as conn:
        settings_count = conn.execute(
            "SELECT COUNT(*) AS n FROM telegram_notification_settings WHERE user_id = ?;",
            (user_a,),
        ).fetchone()["n"]
        cards_count = conn.execute(
            "SELECT COUNT(*) AS n FROM telegram_opportunity_cards WHERE user_id = ?;",
            (user_a,),
        ).fetchone()["n"]
        outbox_count = conn.execute(
            "SELECT COUNT(*) AS n FROM telegram_notification_outbox WHERE user_id = ?;",
            (user_a,),
        ).fetchone()["n"]

    assert settings_count == 0
    assert cards_count == 0
    assert outbox_count == 0

    assert repo.get_settings(user_b).chat_id == CHAT_ID
    assert repo.list_current_cards(user_b)
    assert [record.user_id for record in repo.list_due_actions(NOW)] == [user_b]


@pytest.fixture
def repo(tmp_path: Path, notif) -> NotificationRepository:
    db_path = _db_path(tmp_path)
    init_db(db_path)
    auth = AuthRepository(db_path)
    auth.register_user("alice", FAKE_PASSWORD)
    repository = NotificationRepository(db_path)
    repository.get_settings(notif.USER_ID)
    return repository


@pytest.fixture
def card_factory(notif):
    def build(**overrides) -> CardState:
        return notif.card_state(**overrides)

    return build


@pytest.fixture
def draft_factory(notif):
    def build(action: OutboxActionType, **overrides) -> OutboxActionDraft:
        base = {
            "action": action,
            "text": f"text-{action.value}",
            "fingerprint": action.value,
            "next_attempt_at": notif.NOW,
        }
        base.update(overrides)
        return OutboxActionDraft(**base)

    return build


def test_apply_transitions_writes_every_card_in_one_transaction(repo, card_factory) -> None:
    first = card_factory(card_key="event-1:1X2")
    second = card_factory(card_key="event-1:BTTS")
    repo.apply_transitions([(first, ()), (second, ())])

    cards = repo.list_current_cards(first.user_id)
    assert set(cards) == {"event-1:1X2", "event-1:BTTS"}


def test_list_current_cards_returns_only_the_newest_generation(repo, card_factory) -> None:
    repo.apply_transitions([(card_factory(generation=1, status=LifecycleStatus.CLOSED), ())])
    repo.apply_transitions([(card_factory(generation=2, status=LifecycleStatus.ACTIVE), ())])

    cards = repo.list_current_cards(1)
    assert cards["event-1:1X2"].generation == 2


def test_due_actions_put_sends_before_edits(repo, card_factory, draft_factory, notif) -> None:
    state = card_factory()
    repo.apply_transitions(
        [
            (
                state,
                (
                    draft_factory(OutboxActionType.EDIT_CARD),
                    draft_factory(OutboxActionType.SEND_ALERT),
                    draft_factory(OutboxActionType.SEND_CLOSED),
                ),
            )
        ]
    )
    actions = [record.action for record in repo.list_due_actions(notif.NOW)]
    assert actions.index(OutboxActionType.SEND_CLOSED) < actions.index(OutboxActionType.SEND_ALERT)
    assert actions.index(OutboxActionType.SEND_ALERT) < actions.index(OutboxActionType.EDIT_CARD)


def test_fail_action_takes_the_row_out_of_the_pending_queue(
    repo, card_factory, draft_factory, notif
) -> None:
    state = card_factory()
    repo.apply_transitions([(state, (draft_factory(OutboxActionType.SEND_CARD),))])
    record = repo.list_due_actions(notif.NOW)[0]

    repo.fail_action(record.outbox_id, "Telegram HTTP 403")

    assert repo.list_due_actions(notif.NOW) == ()


def test_disable_for_user_clears_pending_work_and_records_the_reason(
    repo, card_factory, draft_factory, notif
) -> None:
    state = card_factory()
    repo.apply_transitions([(state, (draft_factory(OutboxActionType.SEND_CARD),))])

    repo.disable_for_user(state.user_id, "Bot sohbetten engellenmiş, yeniden bağlayın")

    settings = repo.get_settings(state.user_id)
    assert settings.enabled is False
    assert "engellenmiş" in settings.last_error
    assert repo.list_due_actions(notif.NOW) == ()


def test_marking_an_edit_delivered_stamps_last_edit_at(
    repo, card_factory, draft_factory, notif
) -> None:
    state = card_factory(message_id=555)
    repo.apply_transitions([(state, (draft_factory(OutboxActionType.EDIT_CARD),))])
    record = repo.list_due_actions(notif.NOW)[0]

    repo.mark_action_delivered(record.outbox_id, notif.NOW, message_id=555)

    assert repo.list_current_cards(state.user_id)["event-1:1X2"].last_edit_at == notif.NOW


def test_due_actions_populate_card_key_message_id_and_silent(
    repo, card_factory, draft_factory, notif
) -> None:
    state = card_factory()
    repo.apply_transitions(
        [
            (
                state,
                (
                    draft_factory(
                        OutboxActionType.SEND_ALERT,
                        message_id=123,
                        silent=True,
                    ),
                ),
            )
        ]
    )
    records = repo.list_due_actions(notif.NOW)
    assert len(records) == 1
    assert records[0].card_key == state.card_key
    assert records[0].message_id == 123
    assert records[0].silent is True


def test_marking_send_card_delivered_sets_message_id_and_last_edit_at(
    repo, card_factory, draft_factory, notif
) -> None:
    state = card_factory(message_id=None)
    repo.apply_transitions([(state, (draft_factory(OutboxActionType.SEND_CARD),))])
    record = repo.list_due_actions(notif.NOW)[0]

    repo.mark_action_delivered(record.outbox_id, notif.NOW, message_id=777)

    card = repo.list_current_cards(state.user_id)["event-1:1X2"]
    assert card.message_id == 777
    assert card.last_edit_at == notif.NOW


def test_coalesce_key_for_card_actions(notif, draft_factory) -> None:
    card = notif.card_state()
    draft_send = draft_factory(OutboxActionType.SEND_CARD)
    draft_edit = draft_factory(OutboxActionType.EDIT_CARD)
    draft_alert = draft_factory(OutboxActionType.SEND_ALERT)
    draft_revived = draft_factory(OutboxActionType.SEND_REVIVED)
    draft_closed = draft_factory(OutboxActionType.SEND_CLOSED)

    assert _coalesce_key(card, draft_send) == f"card:1:123:{card.card_key}:1"
    assert _coalesce_key(card, draft_edit) == f"card:1:123:{card.card_key}:1"
    assert _coalesce_key(card, draft_alert) == f"alert:1:123:{card.card_key}:1:na"
    card_alerted = card.model_copy(update={"last_alert_at": notif.NOW})
    assert (
        _coalesce_key(card_alerted, draft_alert)
        == f"alert:1:123:{card.card_key}:1:{notif.NOW.isoformat()}"
    )
    assert _coalesce_key(card, draft_revived) == f"revived:1:123:{card.card_key}:1:0"
    assert _coalesce_key(card, draft_closed) == f"closed:1:123:{card.card_key}:1"


def test_update_settings_purges_pending_market_actions(tmp_path: Path, notif) -> None:
    from surebet.domain.enums import MarketType

    repo, user, _ = initialized_repo(tmp_path)
    repo.update_settings(user, TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID))

    card_1x2 = _card(notif, user, market_type=MarketType.FULL_TIME_RESULT)
    card_ou25 = _card(notif, user, market_type=MarketType.TOTAL_GOALS_25)

    repo.apply_transitions(
        [
            (card_1x2, (_draft(OutboxActionType.SEND_CARD, profit_bps=120),)),
            (card_ou25, (_draft(OutboxActionType.SEND_CARD, profit_bps=150),)),
        ]
    )

    due = repo.list_due_actions(NOW)
    assert len(due) == 2

    # Disable OU25: pending OU25 action is immediately deleted from outbox
    repo.update_settings(user, TelegramSettingsUpdate(notify_ou25=False))

    due_after = repo.list_due_actions(NOW)
    assert len(due_after) == 1
    assert "1X2" in due_after[0].card_key

    # Disable 1X2: pending 1X2 action is also deleted
    repo.update_settings(user, TelegramSettingsUpdate(notify_1x2=False))
    assert repo.list_due_actions(NOW) == ()


def test_list_due_actions_filters_disabled_market(tmp_path: Path, notif) -> None:
    from surebet.domain.enums import MarketType

    repo, user, _ = initialized_repo(tmp_path)
    repo.update_settings(
        user,
        TelegramSettingsUpdate(enabled=True, chat_id=CHAT_ID, notify_ou25=False),
    )

    card_ou25 = _card(notif, user, market_type=MarketType.TOTAL_GOALS_25)
    repo.apply_transitions([(card_ou25, (_draft(OutboxActionType.SEND_CARD, profit_bps=150),))])

    assert repo.list_due_actions(NOW) == ()


def test_purge_completed_outbox(repo, card_factory, draft_factory, notif) -> None:
    from datetime import timedelta

    from surebet.storage.db import get_connection

    now = notif.NOW
    eight_days_ago = (now - timedelta(days=8)).isoformat()
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    cutoff = now - timedelta(days=7)

    with get_connection(repo._db_path) as conn:
        # 1. 8 gun once teslim edilmis -> silinmeli
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'send_card', 'delivered', 0, 1, ?, '{}', ?, ?);",
            (eight_days_ago, eight_days_ago, eight_days_ago),
        )
        # 2. 8 gun once superseded olmus -> silinmeli
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'edit_card', 'superseded', 1, 0, ?, '{}', ?, ?);",
            (eight_days_ago, eight_days_ago, eight_days_ago),
        )
        # 3. 8 gun once failed olmus -> korunmali
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'send_card', 'failed', 0, 5, ?, '{}', ?, ?);",
            (eight_days_ago, eight_days_ago, eight_days_ago),
        )
        # 4. 1 saat once teslim edilmis -> korunmali
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'send_card', 'delivered', 0, 1, ?, '{}', ?, ?);",
            (one_hour_ago, one_hour_ago, one_hour_ago),
        )
        # 5. Pending satir -> korunmali
        conn.execute(
            "INSERT INTO telegram_notification_outbox "
            "(user_id, chat_id, card_key, matched_id, generation, action, status, priority, "
            "attempt_count, next_attempt_at, payload_json, created_at, updated_at) "
            "VALUES (1, 123, 'e1:1X2', 'e1', 1, 'send_card', 'pending', 0, 0, ?, '{}', ?, ?);",
            (eight_days_ago, eight_days_ago, eight_days_ago),
        )

    deleted_count = repo.purge_completed_outbox(cutoff)
    assert deleted_count == 2

    with get_connection(repo._db_path) as conn:
        statuses = [
            row["status"]
            for row in conn.execute(
                "SELECT status FROM telegram_notification_outbox ORDER BY outbox_id;"
            ).fetchall()
        ]
    assert statuses == ["failed", "delivered", "pending"]


def test_coalescing_pending_send_card_with_edit_draft_preserves_send_action_and_priority(
    repo, card_factory, draft_factory, notif
) -> None:
    from surebet.storage.db import get_connection

    card = card_factory(message_id=None)
    # 1. SEND_CARD draft applied -> pending send_card with priority 0
    draft_send = draft_factory(OutboxActionType.SEND_CARD)
    repo.apply_transitions([(card, (draft_send,))])

    with get_connection(repo._db_path) as conn:
        row = conn.execute(
            "SELECT action, priority, status FROM telegram_notification_outbox WHERE card_key = ?;",
            (card.card_key,),
        ).fetchone()
    assert row["action"] == "send_card"
    assert row["priority"] == 0
    assert row["status"] == "pending"

    # 2. EDIT_CARD draft applied to the same card -> coalesce key matches
    draft_edit = draft_factory(OutboxActionType.EDIT_CARD)
    repo.apply_transitions([(card, (draft_edit,))])

    # 3. Verify that action remains 'send_card' and priority remains 0
    with get_connection(repo._db_path) as conn:
        row = conn.execute(
            "SELECT action, priority, status FROM telegram_notification_outbox WHERE card_key = ?;",
            (card.card_key,),
        ).fetchone()
    assert row["action"] == "send_card"
    assert row["priority"] == 0
    assert row["status"] == "pending"


def test_get_card_message_id(repo, card_factory, notif) -> None:
    # 1. Kart yoksa None döner
    assert repo.get_card_message_id(user_id=1, card_key="non-existent") is None

    # 2. Kart var ama message_id None ise None döner
    card_gen1_no_msg = card_factory(message_id=None, generation=1)
    repo.apply_transitions([(card_gen1_no_msg, ())])
    assert (
        repo.get_card_message_id(
            user_id=card_gen1_no_msg.user_id, card_key=card_gen1_no_msg.card_key
        )
        is None
    )

    # 3. Kart var ve message_id varsa döner
    card_gen1_with_msg = card_factory(message_id=456, generation=1)
    repo.apply_transitions([(card_gen1_with_msg, ())])
    assert (
        repo.get_card_message_id(
            user_id=card_gen1_with_msg.user_id, card_key=card_gen1_with_msg.card_key
        )
        == 456
    )

    # 4. Birden fazla generation varsa en son generation'ın message_id'sini döner
    card_gen2 = card_factory(message_id=789, generation=2)
    repo.apply_transitions([(card_gen2, ())])
    assert repo.get_card_message_id(user_id=card_gen2.user_id, card_key=card_gen2.card_key) == 789

    # 5. Başka kullanıcının kartını dönmez
    assert repo.get_card_message_id(user_id=999, card_key=card_gen2.card_key) is None


def test_mute_card_lifecycle(tmp_path: Path) -> None:
    repo, _, _ = initialized_repo(tmp_path)
    chat_id = 998877
    card_key = "match-1:1X2"

    assert repo.is_card_muted(chat_id, card_key) is False

    until = repo.mute_card(chat_id, card_key, duration_seconds=100)
    assert until is not None
    assert repo.is_card_muted(chat_id, card_key) is True

    # Unmute
    repo.unmute_card(chat_id, card_key)
    assert repo.is_card_muted(chat_id, card_key) is False


def test_favorites_lifecycle(tmp_path: Path) -> None:
    repo, _, _ = initialized_repo(tmp_path)
    chat_id = 998877
    card_key1 = "match-1:1X2"
    card_key2 = "match-2:OU25"

    assert repo.list_favorites(chat_id) == ()
    assert repo.is_favorite(chat_id, card_key1) is False

    # Add card 1
    added = repo.toggle_favorite(chat_id, card_key1)
    assert added is True
    assert repo.is_favorite(chat_id, card_key1) is True
    assert repo.list_favorites(chat_id) == (card_key1,)

    # Add card 2
    added2 = repo.toggle_favorite(chat_id, card_key2)
    assert added2 is True
    assert set(repo.list_favorites(chat_id)) == {card_key1, card_key2}

    # Remove card 1
    removed = repo.toggle_favorite(chat_id, card_key1)
    assert removed is False
    assert repo.is_favorite(chat_id, card_key1) is False
    assert repo.list_favorites(chat_id) == (card_key2,)

"""End-to-end smoke test: service -> repository -> dispatcher -> Telegram Bot API.

Drives the real multi-user `TelegramNotificationService.evaluate_cycle`
coordinator over the shared `app_context` fixture (see `conftest.py`) and
asserts the Bot API call shape the design specifies: one card per market,
alerts threaded as replies to their card, a full decline driven through to
closure, the source-outage rule that suspends closure (spec section 5.3), one
card-table read per user per cycle, and silence when nothing changed.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from surebet.domain.enums import MarketType, Source
from surebet.notifications.models import LifecycleStatus, card_key
from surebet.storage.snapshots import SourceHealthStatus

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# Below the default close floor (0.80 * 95 bps = 76 bps), so a card that stays
# here past CLOSE_GRACE_SECONDS must close.
COLLAPSED_BPS = 40

_FTR_KEY = card_key("smoke-event-1", MarketType.FULL_TIME_RESULT)
_OU25_KEY = card_key("smoke-event-1", MarketType.TOTAL_GOALS_25)


def _cards(app_context):
    [settings] = app_context.repo.list_enabled_settings()
    return app_context.repo.list_current_cards(settings.user_id)


def _health(status: SourceHealthStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status)


async def test_two_markets_produce_two_threaded_conversations(app_context) -> None:
    service, dispatcher, client = app_context.service, app_context.dispatcher, app_context.client

    service.evaluate_cycle(NOW)
    await dispatcher.run_once()

    assert len({message["chat_id"] for message in client.sent}) == 1
    assert len(client.sent) == 2  # iki kart, iki ayrı mesaj

    card_message_ids = {message["message_id"] for message in client.responses}

    service.evaluate_cycle(NOW + timedelta(minutes=6))
    await dispatcher.run_once()

    alerts = [message for message in client.sent if "→" in message["text"]]
    assert alerts
    for alert in alerts:
        assert alert["reply_to_message_id"] in card_message_ids


async def test_a_full_decline_produces_the_designed_bot_api_call_sequence(app_context) -> None:
    """One card driven ACTIVE -> ACTIVE+alert -> WATCH -> CLOSED through the real
    repository and dispatcher, asserting the Bot API calls the design specifies."""
    service, dispatcher = app_context.service, app_context.dispatcher
    repo, client = app_context.repo, app_context.client

    # Cycle 1 -- NEW: one sendMessage per market, no edits yet.
    service.evaluate_cycle(NOW)
    await dispatcher.run_once()
    assert len(client.sent) == 2
    assert client.edited == []
    card_message_ids = {message["message_id"] for message in client.responses}

    # Cycle 2 -- ACTIVE with a 30bps drop past the alert threshold: the card is
    # edited in place and an alert is threaded underneath it.
    service.evaluate_cycle(NOW + timedelta(minutes=6))
    await dispatcher.run_once()
    assert len(client.edited) == 2
    alerts = [message for message in client.sent[2:] if "→" in message["text"]]
    assert len(alerts) == 2
    assert all(alert["reply_to_message_id"] in card_message_ids for alert in alerts)

    # Cycle 3 -- profit collapses below the close floor: WATCH, edit only, and
    # crucially NOT closed yet (the grace window has not elapsed).
    app_context.profit[MarketType.FULL_TIME_RESULT] = COLLAPSED_BPS
    app_context.profit[MarketType.TOTAL_GOALS_25] = COLLAPSED_BPS
    sent_before_watch = len(client.sent)
    service.evaluate_cycle(NOW + timedelta(minutes=10))
    await dispatcher.run_once()

    assert _cards(app_context)[_FTR_KEY].status is LifecycleStatus.WATCH
    assert len(client.sent) == sent_before_watch  # nothing announced while watching
    assert len(client.edited) == 4

    # Cycle 4 -- still collapsed once the grace window has passed: CLOSED, with a
    # final edit plus a closing message threaded under the card.
    service.evaluate_cycle(NOW + timedelta(minutes=14))
    await dispatcher.run_once()

    closed_card = _cards(app_context)[_FTR_KEY]
    assert closed_card.status is LifecycleStatus.CLOSED
    assert closed_card.closed_at == NOW + timedelta(minutes=14)

    closing = [message for message in client.sent if message["text"].startswith("🔴 Kapandı")]
    assert len(closing) == 2
    assert all(message["reply_to_message_id"] in card_message_ids for message in closing)
    assert len(client.edited) == 6

    # Every queued action was consumed; nothing is left retrying.
    assert repo.list_due_actions(NOW + timedelta(hours=1)) == ()


async def test_a_relevant_source_outage_suspends_closure(app_context) -> None:
    """Spec section 5.3: frozen data must not close a card. Source A is a relevant
    source for the 1X2 assignment but not for the two-way OU25 one, so taking it
    offline must stall exactly the 1X2 card -- which also proves the service's
    per-source health mapping reaches the engine intact."""
    service, dispatcher, client = app_context.service, app_context.dispatcher, app_context.client

    service.evaluate_cycle(NOW)
    await dispatcher.run_once()

    # Collapse both markets below the close floor while every source is healthy.
    app_context.profit[MarketType.FULL_TIME_RESULT] = COLLAPSED_BPS
    app_context.profit[MarketType.TOTAL_GOALS_25] = COLLAPSED_BPS
    service.evaluate_cycle(NOW + timedelta(minutes=10))
    await dispatcher.run_once()
    assert _cards(app_context)[_FTR_KEY].status is LifecycleStatus.WATCH

    # Source A goes down. The grace window has long elapsed by the next cycle, so
    # without the health rule both cards would close here.
    app_context.catalog.health[Source.SOURCE_A] = _health(SourceHealthStatus.DEGRADED)
    sent_before_outage = len(client.sent)
    service.evaluate_cycle(NOW + timedelta(minutes=20))
    await dispatcher.run_once()

    cards = _cards(app_context)
    assert cards[_FTR_KEY].status is LifecycleStatus.STALE
    assert cards[_FTR_KEY].closed_at is None
    # The OU25 assignment never touches Source A, so it is unaffected and closes.
    assert cards[_OU25_KEY].status is LifecycleStatus.CLOSED

    closing = [message for message in client.sent if message["text"].startswith("🔴 Kapandı")]
    assert len(closing) == 1  # only the OU25 card announced a close

    # Once Source A recovers the grace counter restarts from the first FRESH
    # below-floor observation: the unobserved minutes of the outage must not be
    # spendable toward CLOSE_GRACE_SECONDS, so this cycle only watches.
    app_context.catalog.health[Source.SOURCE_A] = _health(SourceHealthStatus.HEALTHY)
    service.evaluate_cycle(NOW + timedelta(minutes=30))
    await dispatcher.run_once()

    assert _cards(app_context)[_FTR_KEY].status is LifecycleStatus.WATCH

    # A full grace window of healthy, still-collapsed observations closes it.
    service.evaluate_cycle(NOW + timedelta(minutes=34))
    await dispatcher.run_once()

    assert _cards(app_context)[_FTR_KEY].status is LifecycleStatus.CLOSED
    assert len(client.sent) > sent_before_outage


async def test_one_cycle_reads_the_card_table_once_per_user(app_context, query_counter) -> None:
    with query_counter() as counted:
        app_context.service.evaluate_cycle(NOW)

    selects = [sql for sql in counted if sql.lstrip().upper().startswith("SELECT")]
    card_reads = [sql for sql in selects if "telegram_opportunity_cards" in sql]
    assert len(card_reads) == 1


async def test_a_quiet_cycle_sends_nothing(app_context) -> None:
    service, dispatcher, client = app_context.service, app_context.dispatcher, app_context.client

    service.evaluate_cycle(NOW)
    await dispatcher.run_once()
    sent_after_open = len(client.sent)

    service.evaluate_cycle(NOW + timedelta(seconds=30))
    await dispatcher.run_once()

    assert len(client.sent) == sent_after_open

"""Tests for TelegramBotListener, text parsing, and interactive calculation flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from surebet.domain.enums import Outcome
from surebet.notifications.listener import (
    TelegramBotListener,
    format_allocation_response,
    parse_calculator_command,
    parse_cents,
    resolve_outcome,
)
from surebet.notifications.telegram import TelegramResult
from surebet.opportunities.allocation_fixed import calculate_fixed_stake_allocation

# --- Tests for parse_cents ---


def test_parse_cents_integer_strings() -> None:
    assert parse_cents("3500") == 350000
    assert parse_cents("3.500") == 350000
    assert parse_cents("3500 TL") == 350000
    assert parse_cents(" 10.000 tl ") == 1000000


def test_parse_cents_decimal_strings() -> None:
    assert parse_cents("3500,50") == 350050
    assert parse_cents("3.500,50 TL") == 350050
    assert parse_cents("3500.50") == 350050


def test_parse_cents_invalid() -> None:
    assert parse_cents("") is None
    assert parse_cents("abc") is None
    assert parse_cents("-100") is None


# --- Tests for parse_calculator_command ---


def test_parse_calculator_command_pure_amount() -> None:
    req = parse_calculator_command("3500")
    assert req is not None
    assert req.amount_cents == 350000
    assert req.is_budget is False
    assert req.outcome_raw is None


def test_parse_calculator_command_outcome_prefix() -> None:
    req = parse_calculator_command("ms1 2500")
    assert req is not None
    assert req.amount_cents == 250000
    assert req.is_budget is False
    assert req.outcome_raw == "ms1"

    req_x = parse_calculator_command("x 1500 TL")
    assert req_x is not None
    assert req_x.amount_cents == 150000
    assert req_x.outcome_raw == "x"

    req_over = parse_calculator_command("üst 3000")
    assert req_over is not None
    assert req_over.amount_cents == 300000
    assert req_over.outcome_raw == "üst"


def test_parse_calculator_command_outcome_suffix() -> None:
    req = parse_calculator_command("2500 ms1")
    assert req is not None
    assert req.amount_cents == 250000
    assert req.outcome_raw == "ms1"


def test_parse_calculator_command_budget() -> None:
    req = parse_calculator_command("bütçe 10000")
    assert req is not None
    assert req.amount_cents == 1000000
    assert req.is_budget is True

    req2 = parse_calculator_command("butce 5.000 TL")
    assert req2 is not None
    assert req2.amount_cents == 500000
    assert req2.is_budget is True


def test_parse_calculator_command_sabit() -> None:
    # User case: "X sabit 300000"
    req1 = parse_calculator_command("X sabit 300000")
    assert req1 is not None
    assert req1.amount_cents == 30000000
    assert req1.is_budget is False
    assert req1.outcome_raw == "x"

    # Prefix sabit: "sabit x 300000"
    req2 = parse_calculator_command("sabit x 300000")
    assert req2 is not None
    assert req2.amount_cents == 30000000
    assert req2.outcome_raw == "x"

    # Suffix sabit: "300000 x sabit"
    req3 = parse_calculator_command("300000 x sabit")
    assert req3 is not None
    assert req3.amount_cents == 30000000
    assert req3.outcome_raw == "x"

    # With sabitle: "x sabitle 300.000 TL"
    req4 = parse_calculator_command("x sabitle 300.000 TL")
    assert req4 is not None
    assert req4.amount_cents == 30000000
    assert req4.outcome_raw == "x"

    # MS1 with sabit: "ms1 sabit 2000"
    req5 = parse_calculator_command("ms1 sabit 2000")
    assert req5 is not None
    assert req5.amount_cents == 200000
    assert req5.outcome_raw == "ms1"

    # Just sabit and amount (defaults to primary): "sabit 300000"
    req6 = parse_calculator_command("sabit 300000")
    assert req6 is not None
    assert req6.amount_cents == 30000000
    assert req6.outcome_raw is None

    # Slash command: "/sabit x 300000"
    req7 = parse_calculator_command("/sabit x 300000")
    assert req7 is not None
    assert req7.amount_cents == 30000000
    assert req7.outcome_raw == "x"

    # Multi-word outcome: "ms 2 sabit 1500"
    req8 = parse_calculator_command("ms 2 sabit 1500")
    assert req8 is not None
    assert req8.amount_cents == 150000
    assert req8.outcome_raw == "ms2"


def test_parse_cents_rejects_words() -> None:
    assert parse_cents("x sabit 300000") is None
    assert parse_cents("merhaba 5000") is None
    assert parse_cents("kurtar 1000") is None


def test_resolve_outcome(notif) -> None:
    opp = notif.opportunity(240)
    assert resolve_outcome(opp, "ms1") == Outcome.HOME
    assert resolve_outcome(opp, "1") == Outcome.HOME
    assert resolve_outcome(opp, "x") == Outcome.DRAW
    assert resolve_outcome(opp, "beraberlik") == Outcome.DRAW
    assert resolve_outcome(opp, "ms2") == Outcome.AWAY
    assert resolve_outcome(opp, None) == Outcome.HOME


def test_format_allocation_response(notif) -> None:
    opp = notif.opportunity(240)
    odds = {out: s.odds for out, s in opp.selections.items()}
    sources = {out: s.source for out, s in opp.selections.items()}
    result = calculate_fixed_stake_allocation(
        fixed_outcome=Outcome.HOME, fixed_stake_cents=350000, odds=odds, sources=sources
    )
    text, markup = format_allocation_response(opp, result)

    assert "Bahis Dağılım Hesabı" in text
    assert "Galatasaray - Fenerbahçe" in text
    assert "Sabitlenen:</b> MS1" in text
    assert "3.500,00 TL" in text
    assert "Hesaplanan Net Fark:" in text

    keyboard = markup.get("inline_keyboard", [])
    assert len(keyboard) >= 1
    buttons = [b for row in keyboard for b in row]
    assert len(buttons) == 2  # recalc + help; the bot never links out to a sportsbook
    assert all("url" not in b for b in buttons)
    assert any("Örnek Komutlar" in b["text"] for b in buttons)


# --- Tests for TelegramBotListener execution ---


@pytest.mark.asyncio
async def test_listener_handles_help_command(notif) -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_message({"chat": {"id": 123}, "text": "/help", "message_id": 1})

    client.send_text.assert_awaited_once()
    call_args = client.send_text.await_args
    assert "Arbitraj Asistanı" in call_args.kwargs["text"]
    assert call_args.kwargs["chat_id"] == 123


@pytest.mark.asyncio
async def test_listener_handles_help_callback(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb1",
            "data": "help:some_key",
            "message": {"message_id": 100, "chat": {"id": 123}},
        }
    )
    client.answer_callback_query.assert_awaited_once_with("cb1")
    client.send_text.assert_awaited_once()
    assert "Örnek Hesaplama Komutları" in client.send_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_reply_calculation(notif) -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_message_id.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_message(
        {
            "chat": {"id": 123},
            "text": "3500",
            "message_id": 10,
            "reply_to_message": {"message_id": 555},
        }
    )

    repo.get_card_by_message_id.assert_called_once_with(123, 555)
    client.send_text.assert_awaited_once()
    call_args = client.send_text.await_args
    assert "Bahis Dağılım Hesabı" in call_args.kwargs["text"]
    assert "3.500,00 TL" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_reply_calculation_sabit_x(notif) -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_message_id.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_message(
        {
            "chat": {"id": 123},
            "text": "X sabit 300000",
            "message_id": 10,
            "reply_to_message": {"message_id": 555},
        }
    )

    client.send_text.assert_awaited_once()
    call_args = client.send_text.await_args
    assert "Bahis Dağılım Hesabı" in call_args.kwargs["text"]
    assert "<b>Sabitlenen:</b> X" in call_args.kwargs["text"]
    assert "300.000,00 TL" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_calc_callback_query(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_key.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb-99",
            "data": f"calc:{card_state.card_key}",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )

    client.answer_callback_query.assert_awaited_once_with("cb-99")
    client.send_text.assert_awaited_once()
    call_args = client.send_text.await_args
    assert "Bahis Dağılım Hesaplayıcı" in call_args.kwargs["text"]
    assert "inline_keyboard" in call_args.kwargs["reply_markup"]


@pytest.mark.asyncio
async def test_listener_handles_do_calc_callback_query(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_key.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb-100",
            "data": f"do_calc|{card_state.card_key}|home|500000",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )

    client.answer_callback_query.assert_awaited_once_with("cb-100", text="Hesaplandı!")
    client.send_text.assert_awaited_once()
    call_args = client.send_text.await_args
    assert "Bahis Dağılım Hesabı" in call_args.kwargs["text"]
    assert "5.000,00 TL" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_listener_handles_refresh_callback(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_key.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb-ref",
            "data": f"refresh:{card_state.card_key}",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )

    client.answer_callback_query.assert_awaited_once()
    _, kwargs = client.answer_callback_query.await_args
    assert kwargs.get("show_alert") is True
    assert "Anlık Durum" in kwargs.get("text", "")
    assert "Galatasaray - Fenerbahçe" in kwargs.get("text", "")


@pytest.mark.asyncio
async def test_listener_handles_mute_and_unmute(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.edit_reply_markup = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_key.return_value = card_state
    repo.is_favorite.return_value = False

    listener = TelegramBotListener(client, repo)

    # 1. Mute
    await listener._handle_callback_query(
        {
            "id": "cb-mute",
            "data": f"mute:{card_state.card_key}",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )
    repo.mute_card.assert_called_once_with(123, card_state.card_key, duration_seconds=7200)
    client.answer_callback_query.assert_awaited_with(
        "cb-mute", text="Maç 2 saat boyunca susturuldu 🔕"
    )
    client.edit_reply_markup.assert_awaited_once()
    markup = client.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert any(
        "Susturmayı Kaldır" in btn["text"] for row in markup["inline_keyboard"] for btn in row
    )

    # 2. Unmute
    client.edit_reply_markup.reset_mock()
    await listener._handle_callback_query(
        {
            "id": "cb-unmute",
            "data": f"unmute:{card_state.card_key}",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )
    repo.unmute_card.assert_called_once_with(123, card_state.card_key)
    client.answer_callback_query.assert_awaited_with("cb-unmute", text="Susturma kaldırıldı 🔔")
    client.edit_reply_markup.assert_awaited_once()
    markup2 = client.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert any("2 Saat Sustur" in btn["text"] for row in markup2["inline_keyboard"] for btn in row)


@pytest.mark.asyncio
async def test_listener_handles_favorite_toggle(notif) -> None:
    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))
    client.edit_reply_markup = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.get_card_by_key.return_value = card_state
    repo.is_card_muted.return_value = False
    repo.toggle_favorite.return_value = True

    listener = TelegramBotListener(client, repo)
    await listener._handle_callback_query(
        {
            "id": "cb-fav",
            "data": f"fav:{card_state.card_key}",
            "message": {"chat": {"id": 123}, "message_id": 555},
        }
    )
    repo.toggle_favorite.assert_called_once_with(123, card_state.card_key)
    client.answer_callback_query.assert_awaited_with("cb-fav", text="Fırsat favorilere eklendi ⭐")
    client.edit_reply_markup.assert_awaited_once()
    markup = client.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert any("⭐ Takipte" in btn["text"] for row in markup["inline_keyboard"] for btn in row)


@pytest.mark.asyncio
async def test_listener_handles_favorilerim_command(notif) -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    repo = MagicMock()
    card_state = notif.card_state(240)
    repo.list_favorites.return_value = (card_state.card_key,)
    repo.get_card_by_key.return_value = card_state

    listener = TelegramBotListener(client, repo)
    await listener._handle_message(
        {
            "chat": {"id": 123},
            "text": "/favorilerim",
            "message_id": 10,
        }
    )

    client.send_text.assert_awaited_once()
    call_text = client.send_text.await_args.kwargs["text"]
    assert "Favori Fırsatlarınız" in call_text
    assert "Galatasaray - Fenerbahçe" in call_text
    assert "inline_keyboard" in client.send_text.await_args.kwargs["reply_markup"]

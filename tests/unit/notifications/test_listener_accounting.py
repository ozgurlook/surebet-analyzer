"""Tests for TelegramBotListener daily accounting features and UI."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from surebet.accounting.models import (
    AccountingTransaction,
    BankrollAccount,
    DailySummary,
    DayBreakdown,
    TransactionStatus,
    TransactionType,
    WeeklySummary,
)
from surebet.notifications.listener import (
    QuickAccountingCommand,
    TelegramBotListener,
    parse_quick_accounting_command,
    render_accounting_menu,
    render_accounts_list,
    render_daily_report,
    render_recent_transactions,
    render_transaction_confirmed,
    render_weekly_report,
)
from surebet.notifications.render.format import format_lira
from surebet.notifications.telegram import TelegramResult

# --- 1. Quick Accounting Command Parsing Tests ---


def test_parse_quick_accounting_command_deposits() -> None:
    # Basic deposit
    cmd = parse_quick_accounting_command("yatir 5000")
    assert cmd == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date=None
    )

    cmd_turkish = parse_quick_accounting_command("yatır 5000")
    assert cmd_turkish == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date=None
    )

    cmd_en = parse_quick_accounting_command("deposit 5000")
    assert cmd_en == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date=None
    )

    cmd_slash = parse_quick_accounting_command("/yatir 5000")
    assert cmd_slash == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date=None
    )


def test_parse_quick_accounting_command_withdrawals() -> None:
    # Basic withdrawal
    cmd = parse_quick_accounting_command("cekim 8000")
    assert cmd == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date=None
    )

    cmd_turkish = parse_quick_accounting_command("çekim 8000")
    assert cmd_turkish == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date=None
    )

    cmd_en = parse_quick_accounting_command("withdraw 8000")
    assert cmd_en == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date=None
    )


def test_parse_quick_accounting_command_with_dates() -> None:
    # Date at end
    cmd1 = parse_quick_accounting_command("yatir 5000 dun")
    assert cmd1 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date="dun"
    )

    cmd2 = parse_quick_accounting_command("cekim 8000 18.09")
    assert cmd2 == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date="18.09"
    )

    cmd3 = parse_quick_accounting_command("yatir 10k 2026-09-18")
    assert cmd3 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="10k", raw_date="2026-09-18"
    )

    # Date in middle / before amount
    cmd4 = parse_quick_accounting_command("yatir dun 5000")
    assert cmd4 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date="dun"
    )

    cmd5 = parse_quick_accounting_command("cekim 18.09 8000")
    assert cmd5 == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date="18.09"
    )

    cmd6 = parse_quick_accounting_command("yatır dün 10k")
    assert cmd6 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="10k", raw_date="dün"
    )


def test_parse_quick_accounting_command_with_notes() -> None:
    # Amount + Note
    cmd1 = parse_quick_accounting_command("yatir 5000 source_a")
    assert cmd1 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date=None, note="source_a"
    )

    cmd2 = parse_quick_accounting_command("cekim 8000 source_b papara")
    assert cmd2 == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL, amount_str="8000", raw_date=None, note="source_b papara"
    )

    # Date + Amount + Note
    cmd3 = parse_quick_accounting_command("yatir dun 5000 source_c")
    assert cmd3 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000", raw_date="dun", note="source_c"
    )

    cmd4 = parse_quick_accounting_command("cekim 18.09 12000 banka havale")
    assert cmd4 == QuickAccountingCommand(
        tx_type=TransactionType.WITHDRAWAL,
        amount_str="12000",
        raw_date="18.09",
        note="banka havale",
    )

    # Amount with currency + Note
    cmd5 = parse_quick_accounting_command("yatir 5000 TL bonus cevrimi")
    assert cmd5 == QuickAccountingCommand(
        tx_type=TransactionType.DEPOSIT, amount_str="5000 TL", raw_date=None, note="bonus cevrimi"
    )


def test_parse_quick_accounting_command_invalid() -> None:
    assert parse_quick_accounting_command("kurtar ms1 3.10 5000") is None
    assert parse_quick_accounting_command("3500") is None
    assert parse_quick_accounting_command("/domain") is None
    assert parse_quick_accounting_command("yatir") is None
    assert parse_quick_accounting_command("cekim") is None
    assert parse_quick_accounting_command("yatir dun") is None
    assert parse_quick_accounting_command("yatir abc") is None
    assert parse_quick_accounting_command("yatir -500") is None
    assert parse_quick_accounting_command("") is None


# --- 2. Rendering Functions Tests ---


def _dummy_transaction(
    tx_id: int = 1,
    chat_id: int = 12345,
    tx_type: TransactionType = TransactionType.DEPOSIT,
    amount_cents: int = 500_000,
    tx_date: str = "2026-09-19",
    note: str | None = None,
    status: TransactionStatus = TransactionStatus.ACTIVE,
) -> AccountingTransaction:
    return AccountingTransaction(
        id=tx_id,
        chat_id=chat_id,
        tx_type=tx_type,
        amount_cents=amount_cents,
        tx_date=tx_date,
        note=note,
        status=status,
        created_at=datetime(2026, 9, 19, 10, 0, tzinfo=UTC),
    )


def test_render_accounting_menu() -> None:
    daily = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=500_000,
        total_withdrawal_cents=800_000,
        net_profit_cents=300_000,
        transaction_count=2,
        transactions=[],
    )
    weekly = WeeklySummary(
        week_start_str="2026-09-15",
        week_end_str="2026-09-21",
        total_deposit_cents=1_000_000,
        total_withdrawal_cents=1_500_000,
        net_profit_cents=500_000,
        daily_breakdown=[],
    )

    text, markup = render_accounting_menu(daily, weekly)

    assert "Kasa & Günlük Muhasebe" in text
    assert "Bugün:" in text
    assert format_lira(300_000, signed=True) in text
    assert "Bu Hafta:" in text
    assert format_lira(500_000, signed=True) in text

    ik = markup["inline_keyboard"]
    callbacks = [btn["callback_data"] for row in ik for btn in row]
    assert "acc_dep_prompt" in callbacks
    assert "acc_with_prompt" in callbacks
    assert "acc_daily_rep" in callbacks
    assert "acc_weekly_rep" in callbacks
    assert "acc_recent" in callbacks


def test_render_daily_report() -> None:
    tx1 = _dummy_transaction(tx_id=1, tx_type=TransactionType.DEPOSIT, amount_cents=500_000)
    tx2 = _dummy_transaction(tx_id=2, tx_type=TransactionType.WITHDRAWAL, amount_cents=800_000)
    daily = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=500_000,
        total_withdrawal_cents=800_000,
        net_profit_cents=300_000,
        transaction_count=2,
        transactions=[tx1, tx2],
    )

    text, markup = render_daily_report(daily)
    assert "Günlük Muhasebe Raporu" in text
    assert "2026-09-19" in text
    assert format_lira(500_000) in text
    assert format_lira(800_000) in text
    assert format_lira(300_000, signed=True) in text
    assert "#1" in text
    assert "#2" in text

    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "acc_menu" in callbacks


def test_render_weekly_report() -> None:
    breakdown = [
        DayBreakdown(
            date_str="2026-09-15",
            day_name="Pazartesi",
            deposit_cents=100_000,
            withdrawal_cents=150_000,
            net_profit_cents=50_000,
            has_transactions=True,
        ),
        DayBreakdown(
            date_str="2026-09-16",
            day_name="Salı",
            deposit_cents=0,
            withdrawal_cents=0,
            net_profit_cents=0,
            has_transactions=False,
        ),
    ]
    weekly = WeeklySummary(
        week_start_str="2026-09-15",
        week_end_str="2026-09-21",
        total_deposit_cents=100_000,
        total_withdrawal_cents=150_000,
        net_profit_cents=50_000,
        daily_breakdown=breakdown,
    )

    text, markup = render_weekly_report(weekly)
    assert "Haftalık Muhasebe Raporu" in text
    assert "2026-09-15" in text
    assert "Pazartesi" in text
    assert "Salı" in text
    assert format_lira(50_000, signed=True) in text

    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "acc_menu" in callbacks


def test_render_recent_transactions() -> None:
    tx = _dummy_transaction(tx_id=99, amount_cents=750_000)
    text, markup = render_recent_transactions([tx])

    assert "Son Muhasebe İşlemleri" in text
    assert "#99" in text
    assert format_lira(750_000) in text

    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "acc_cancel:99" in callbacks
    assert "acc_menu" in callbacks
    assert markup["inline_keyboard"][0][0]["text"] == f"❌ #99 İptal ({format_lira(750_000)})"

    # With note: note should appear right next to amount and in the cancel button
    tx_note = _dummy_transaction(tx_id=100, amount_cents=500_000, note="duha")
    text2, markup2 = render_recent_transactions([tx_note])
    assert f"<b>{format_lira(500_000)}</b> <i>(duha)</i> · <i>2026-09-19</i>" in text2
    assert (
        markup2["inline_keyboard"][0][0]["text"] == f"❌ #100 İptal ({format_lira(500_000)} · duha)"
    )

    # With cancelled transaction: text should explicitly state "İptal Edildi" and not show cancel button
    tx_cancelled = _dummy_transaction(
        tx_id=101,
        amount_cents=300_000,
        note="deneme",
        status=TransactionStatus.CANCELLED,
    )
    text3, markup3 = render_recent_transactions([tx_cancelled])
    assert "İptal Edildi" in text3
    assert f"<s><b>{format_lira(300_000)}</b></s>" in text3
    assert "(deneme)" in text3
    callbacks3 = [btn["callback_data"] for row in markup3["inline_keyboard"] for btn in row]
    assert "acc_cancel:101" not in callbacks3
    assert "acc_menu" in callbacks3


def test_render_transaction_confirmed() -> None:
    tx = _dummy_transaction(tx_id=42, tx_type=TransactionType.DEPOSIT, amount_cents=1_000_000)
    daily = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=1_000_000,
        total_withdrawal_cents=0,
        net_profit_cents=-1_000_000,
        transaction_count=1,
        transactions=[tx],
    )

    text, markup = render_transaction_confirmed(tx, daily)
    assert "#42" in text
    assert "Yatırım Kaydedildi" in text
    assert format_lira(1_000_000) in text
    assert format_lira(-1_000_000, signed=True) in text

    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "acc_cancel:42" in callbacks
    assert "acc_menu" in callbacks


# --- 3. Listener Message Handling Tests ---


@pytest.fixture
def mock_listener_and_service() -> tuple[TelegramBotListener, MagicMock, MagicMock]:
    client = MagicMock()
    client.send_text = AsyncMock(return_value=TelegramResult(ok=True))
    client.edit_text = AsyncMock(return_value=TelegramResult(ok=True))
    client.answer_callback_query = AsyncMock(return_value=TelegramResult(ok=True))

    repo = MagicMock()
    repo._db_path = ":memory:"

    service = MagicMock()
    listener = TelegramBotListener(client, repo, accounting_service=service)
    return listener, client, service


@pytest.mark.asyncio
async def test_handle_message_muhasebe_command(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        transaction_count=0,
        transactions=[],
    )
    service.get_weekly_summary.return_value = WeeklySummary(
        week_start_str="2026-09-15",
        week_end_str="2026-09-21",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        daily_breakdown=[],
    )

    await listener._handle_message({"chat": {"id": 123}, "text": "/muhasebe", "message_id": 10})

    service.get_daily_summary.assert_called_once_with(123)
    service.get_weekly_summary.assert_called_once_with(123)
    client.send_text.assert_awaited_once()
    assert "Kasa & Günlük Muhasebe" in client.send_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_message_iptal_command(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    tx = _dummy_transaction(tx_id=15)
    service.cancel.return_value = tx
    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        transaction_count=0,
        transactions=[],
    )

    # Specific ID: /iptal 15
    await listener._handle_message({"chat": {"id": 123}, "text": "/iptal 15", "message_id": 11})
    service.cancel.assert_called_once_with(chat_id=123, tx_id=15)
    client.send_text.assert_awaited_once()
    assert "#15" in client.send_text.await_args.kwargs["text"]
    assert "İptal Edildi" in client.send_text.await_args.kwargs["text"]

    # Latest: /iptal without args
    client.send_text.reset_mock()
    service.cancel_latest.return_value = tx
    await listener._handle_message({"chat": {"id": 123}, "text": "/iptal", "message_id": 12})
    service.cancel_latest.assert_called_once_with(chat_id=123)
    client.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_quick_command(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    tx = _dummy_transaction(tx_id=88, amount_cents=500_000)
    service.record.return_value = tx
    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=500_000,
        total_withdrawal_cents=0,
        net_profit_cents=-500_000,
        transaction_count=1,
        transactions=[tx],
    )

    await listener._handle_message({"chat": {"id": 123}, "text": "yatir 5000", "message_id": 20})

    service.record.assert_called_once_with(
        chat_id=123,
        tx_type=TransactionType.DEPOSIT,
        amount="5000",
        raw_date=None,
        note=None,
    )
    client.send_text.assert_awaited_once()
    assert "#88" in client.send_text.await_args.kwargs["text"]

    # Test with note
    service.record.reset_mock()
    await listener._handle_message(
        {"chat": {"id": 123}, "text": "yatir 5000 source_a", "message_id": 21}
    )
    service.record.assert_called_once_with(
        chat_id=123,
        tx_type=TransactionType.DEPOSIT,
        amount="5000",
        raw_date=None,
        note="source_a",
    )


@pytest.mark.asyncio
async def test_handle_message_pending_prompt(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    listener._pending_accounting_inputs[123] = (TransactionType.DEPOSIT, "2026-09-19")

    tx = _dummy_transaction(tx_id=101, amount_cents=1_000_000, note="papara")
    service.record.return_value = tx
    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=1_000_000,
        total_withdrawal_cents=0,
        net_profit_cents=-1_000_000,
        transaction_count=1,
        transactions=[tx],
    )

    # Send amount and note while in pending state
    await listener._handle_message({"chat": {"id": 123}, "text": "10k papara", "message_id": 30})

    service.record.assert_called_once_with(
        chat_id=123,
        tx_type=TransactionType.DEPOSIT,
        amount=1_000_000,
        raw_date="2026-09-19",
        note="papara",
    )
    assert 123 not in listener._pending_accounting_inputs
    client.send_text.assert_awaited_once()
    assert "#101" in client.send_text.await_args.kwargs["text"]


# --- 4. Listener Callback Query Handling Tests ---


@pytest.mark.asyncio
async def test_handle_callback_acc_menu(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        transaction_count=0,
        transactions=[],
    )
    service.get_weekly_summary.return_value = WeeklySummary(
        week_start_str="2026-09-15",
        week_end_str="2026-09-21",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        daily_breakdown=[],
    )

    await listener._handle_callback_query(
        {
            "id": "cb-1",
            "data": "acc_menu",
            "message": {"chat": {"id": 123}, "message_id": 50},
        }
    )

    client.answer_callback_query.assert_awaited_with("cb-1")
    client.edit_text.assert_awaited_once()
    assert "Kasa & Günlük Muhasebe" in client.edit_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_callback_acc_date_prompt(mock_listener_and_service) -> None:
    listener, client, _service = mock_listener_and_service

    await listener._handle_callback_query(
        {
            "id": "cb-2",
            "data": "acc_dep_prompt",
            "message": {"chat": {"id": 123}, "message_id": 51},
        }
    )

    client.answer_callback_query.assert_awaited_with("cb-2")
    client.edit_text.assert_awaited_once()
    markup = client.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert any(c.startswith("acc_date:DEPOSIT:") for c in callbacks)


@pytest.mark.asyncio
async def test_handle_callback_acc_date_selection(mock_listener_and_service) -> None:
    listener, client, _service = mock_listener_and_service

    await listener._handle_callback_query(
        {
            "id": "cb-3",
            "data": "acc_date:WITHDRAWAL:2026-09-19",
            "message": {"chat": {"id": 123}, "message_id": 52},
        }
    )

    client.answer_callback_query.assert_awaited_with("cb-3")
    assert listener._pending_accounting_inputs[123] == (TransactionType.WITHDRAWAL, "2026-09-19")
    client.edit_text.assert_awaited_once()
    assert "Çekim Tutarı" in client.edit_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_callback_acc_cancel(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service

    tx = _dummy_transaction(tx_id=77, amount_cents=400_000)
    service.cancel.return_value = tx
    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        transaction_count=0,
        transactions=[],
    )

    await listener._handle_callback_query(
        {
            "id": "cb-4",
            "data": "acc_cancel:77",
            "message": {"chat": {"id": 123}, "message_id": 53},
        }
    )

    service.cancel.assert_called_once_with(chat_id=123, tx_id=77)
    client.answer_callback_query.assert_awaited_with("cb-4", text="#77 nolu işlem iptal edildi.")
    client.edit_text.assert_awaited_once()
    assert "#77" in client.edit_text.await_args.kwargs["text"]
    assert "İptal Edildi" in client.edit_text.await_args.kwargs["text"]


def test_render_accounts_list() -> None:
    now = datetime.now(UTC)
    acc1 = BankrollAccount(
        id=1,
        user_id=10,
        account_name="Source B 1",
        initial_balance_cents=100_000,
        current_balance_cents=150_000,
        total_deposit_cents=50_000,
        total_withdrawal_cents=100_000,
        net_profit_cents=50_000,
        note="Ana hesap",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    text, markup = render_accounts_list([acc1])
    assert "Kaynak B Hesaplarım" in text
    assert "Source B 1" in text
    assert format_lira(150_000) in text
    assert format_lira(50_000, signed=True) in text

    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "acc_menu" in callbacks


@pytest.mark.asyncio
async def test_handle_callback_acc_accounts(mock_listener_and_service) -> None:
    listener, client, service = mock_listener_and_service
    listener._repo.get_user_id_by_chat_id.return_value = 10
    now = datetime.now(UTC)
    service.list_accounts.return_value = [
        BankrollAccount(
            id=1,
            user_id=10,
            account_name="Source B VIP",
            initial_balance_cents=500_000,
            current_balance_cents=600_000,
            total_deposit_cents=0,
            total_withdrawal_cents=100_000,
            net_profit_cents=100_000,
            note=None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    ]

    await listener._handle_callback_query(
        {
            "id": "cb-acc",
            "data": "acc_accounts",
            "message": {"chat": {"id": 123}, "message_id": 99},
        }
    )

    client.answer_callback_query.assert_awaited_with("cb-acc")
    service.list_accounts.assert_called_once_with(user_id=10)
    client.edit_text.assert_awaited_once()
    assert "Source B VIP" in client.edit_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_message_quick_command_with_account_resolution(
    mock_listener_and_service,
) -> None:
    listener, client, service = mock_listener_and_service
    listener._repo.get_user_id_by_chat_id.return_value = 42

    now = datetime.now(UTC)
    matched_acc = BankrollAccount(
        id=7,
        user_id=42,
        account_name="Source B 1",
        initial_balance_cents=100_000,
        current_balance_cents=100_000,
        total_deposit_cents=0,
        total_withdrawal_cents=0,
        net_profit_cents=0,
        note=None,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    service.resolve_account.return_value = matched_acc

    tx = _dummy_transaction(tx_id=55, amount_cents=500_000, note="source_b1")
    service.record.return_value = tx
    service.get_daily_summary.return_value = DailySummary(
        date_str="2026-09-20",
        total_deposit_cents=500_000,
        total_withdrawal_cents=0,
        net_profit_cents=-500_000,
        transaction_count=1,
        transactions=[tx],
    )

    await listener._handle_message(
        {"chat": {"id": 123}, "text": "yatir 5000 source_b1", "message_id": 80}
    )

    service.resolve_account.assert_called_once_with(user_id=42, text="source_b1")
    service.record.assert_called_once_with(
        chat_id=123,
        tx_type=TransactionType.DEPOSIT,
        amount="5000",
        raw_date=None,
        note="source_b1",
        account_id=7,
        user_id=42,
    )
    client.send_text.assert_awaited_once()
    sent_text = client.send_text.await_args.kwargs["text"]
    assert "Source B 1" in sent_text
    assert format_lira(500_000) in sent_text

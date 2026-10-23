from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from surebet.accounting.models import TransactionStatus, TransactionType
from surebet.accounting.service import (
    AccountingService,
    parse_amount_to_cents,
    parse_date_input,
)
from surebet.accounting.storage import AccountingRepository
from surebet.storage.db import init_db

ISTANBUL = ZoneInfo("Europe/Istanbul")


def test_parse_amount_to_cents() -> None:
    assert parse_amount_to_cents("5000") == 500_000
    assert parse_amount_to_cents("5.000") == 500_000
    assert parse_amount_to_cents("5000 TL") == 500_000
    assert parse_amount_to_cents("5000 tl") == 500_000
    assert parse_amount_to_cents("₺5000") == 500_000
    assert parse_amount_to_cents("1250,50") == 125_050
    assert parse_amount_to_cents("1250.50") == 125_050
    assert parse_amount_to_cents("10k") == 1_000_000
    assert parse_amount_to_cents("10K") == 1_000_000
    assert parse_amount_to_cents("2.5k") == 250_000
    assert parse_amount_to_cents("2,5k") == 250_000


def test_parse_amount_to_cents_invalid() -> None:
    with pytest.raises(ValueError):
        parse_amount_to_cents("0")
    with pytest.raises(ValueError):
        parse_amount_to_cents("-100")
    with pytest.raises(ValueError):
        parse_amount_to_cents("abc")
    with pytest.raises(ValueError):
        parse_amount_to_cents("")


def test_parse_date_input_smart_year() -> None:
    ref_now = datetime(2026, 9, 19, 12, 0, tzinfo=ISTANBUL)
    assert parse_date_input(None, ref_now) == "2026-09-19"
    assert parse_date_input("", ref_now) == "2026-09-19"
    assert parse_date_input("bugun", ref_now) == "2026-09-19"
    assert parse_date_input("bugün", ref_now) == "2026-09-19"
    assert parse_date_input("dun", ref_now) == "2026-09-18"
    assert parse_date_input("dün", ref_now) == "2026-09-18"
    assert parse_date_input("18.09", ref_now) == "2026-09-18"
    assert parse_date_input("18.09.2027", ref_now) == "2027-09-18"
    assert parse_date_input("2026-09-18", ref_now) == "2026-09-18"

    # Year-transition edge case: on 2027-01-02, entering 31.12 resolves to 2026-12-31
    jan_now = datetime(2027, 1, 2, 10, 0, tzinfo=ISTANBUL)
    assert parse_date_input("31.12", jan_now) == "2026-12-31"


def test_parse_date_input_invalid() -> None:
    with pytest.raises(ValueError):
        parse_date_input("32.01")
    with pytest.raises(ValueError):
        parse_date_input("gecersiz_tarih")


def test_daily_and_weekly_summary_calculations(tmp_path: Path) -> None:
    db = tmp_path / "acc.db"
    init_db(db)
    service = AccountingService(AccountingRepository(db))
    chat_id = 42

    # Monday (2026-09-14) to Sunday (2026-09-20)
    service.record(chat_id, TransactionType.DEPOSIT, 10_000_00, raw_date="2026-09-18")
    service.record(chat_id, TransactionType.WITHDRAWAL, 15_000_00, raw_date="2026-09-18")
    service.record(chat_id, TransactionType.DEPOSIT, "5000", raw_date="2026-09-19")
    service.record(chat_id, TransactionType.WITHDRAWAL, "8000 TL", raw_date="2026-09-19")

    # Daily for 2026-09-19
    daily = service.get_daily_summary(chat_id, "2026-09-19")
    assert daily.total_deposit_cents == 5_000_00
    assert daily.total_withdrawal_cents == 8_000_00
    assert daily.net_profit_cents == 3_000_00
    assert daily.transaction_count == 2
    assert len(daily.transactions) == 2

    # Weekly for 2026-09-19 (in the week 2026-09-14 .. 2026-09-20)
    weekly = service.get_weekly_summary(chat_id, "2026-09-19")
    assert weekly.week_start_str == "2026-09-14"
    assert weekly.week_end_str == "2026-09-20"
    assert weekly.total_deposit_cents == 15_000_00
    assert weekly.total_withdrawal_cents == 23_000_00
    assert weekly.net_profit_cents == 8_000_00
    assert len(weekly.daily_breakdown) == 7

    # Check day breakdown days
    days = [b.day_name for b in weekly.daily_breakdown]
    assert days == ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

    # Friday (2026-09-18) breakdown
    cuma = weekly.daily_breakdown[4]
    assert cuma.date_str == "2026-09-18"
    assert cuma.deposit_cents == 10_000_00
    assert cuma.withdrawal_cents == 15_000_00
    assert cuma.net_profit_cents == 5_000_00
    assert cuma.has_transactions is True

    # Sunday (2026-09-20) breakdown (no transactions)
    pazar = weekly.daily_breakdown[6]
    assert pazar.date_str == "2026-09-20"
    assert pazar.deposit_cents == 0
    assert pazar.withdrawal_cents == 0
    assert pazar.net_profit_cents == 0
    assert pazar.has_transactions is False


def test_cancel_and_cancel_latest(tmp_path: Path) -> None:
    db = tmp_path / "acc.db"
    init_db(db)
    service = AccountingService(AccountingRepository(db))
    chat_id = 101

    tx1 = service.record(chat_id, TransactionType.DEPOSIT, 1_000_00, raw_date="2026-09-19")
    tx2 = service.record(chat_id, TransactionType.WITHDRAWAL, 2_000_00, raw_date="2026-09-19")

    cancelled_tx2 = service.cancel_latest(chat_id)
    assert cancelled_tx2 is not None
    assert cancelled_tx2.id == tx2.id
    assert cancelled_tx2.status == TransactionStatus.CANCELLED

    # Summary should now only reflect tx1
    daily = service.get_daily_summary(chat_id, "2026-09-19")
    assert daily.total_deposit_cents == 1_000_00
    assert daily.total_withdrawal_cents == 0
    assert daily.net_profit_cents == -1_000_00
    assert daily.transaction_count == 1

    # Cancel tx1 explicitly by id
    cancelled_tx1 = service.cancel(chat_id, tx1.id)
    assert cancelled_tx1 is not None
    assert cancelled_tx1.id == tx1.id

    # No more active transactions to cancel
    assert service.cancel_latest(chat_id) is None


def test_get_recent(tmp_path: Path) -> None:
    db = tmp_path / "acc.db"
    init_db(db)
    service = AccountingService(AccountingRepository(db))
    chat_id = 202

    for i in range(10):
        service.record(chat_id, TransactionType.DEPOSIT, 100_00, note=f"Tx {i}")

    recent = service.get_recent(chat_id, limit=3)
    assert len(recent) == 3


def test_service_bankroll_accounts(tmp_path: Path) -> None:
    from surebet.storage.auth import AuthRepository

    db = tmp_path / "acc.db"
    init_db(db)
    service = AccountingService(AccountingRepository(db))

    auth = AuthRepository(db)
    user, _ = auth.register_user("bankroll-user", "correct-horse-battery-staple")

    # 1. Create multiple Source B accounts (e.g. simulating 20-30 accounts)
    accounts = []
    for i in range(1, 26):
        acc = service.create_account(
            user_id=user.user_id,
            account_name=f"Source B {i}",
            initial_balance_cents=100_000 * i,
            note=f"Source B hesap {i}",
        )
        accounts.append(acc)

    assert len(accounts) == 25
    listed = service.list_accounts(user_id=user.user_id)
    assert len(listed) == 25

    # 2. Record deposits and withdrawals on specific accounts
    # Source B 1: Deposit 500 TL (50_000 cents), Withdrawal 200 TL (20_000 cents)
    service.record(
        chat_id=12345,
        tx_type=TransactionType.DEPOSIT,
        amount_raw=50_000,
        raw_date="2026-09-20",
        account_id=accounts[0].id,
        user_id=user.user_id,
    )
    service.record(
        chat_id=12345,
        tx_type=TransactionType.WITHDRAWAL,
        amount_raw=20_000,
        raw_date="2026-09-20",
        account_id=accounts[0].id,
        user_id=user.user_id,
    )

    # Source B 2: Deposit 1000 TL (100_000 cents), Withdrawal 1500 TL (150_000 cents)
    service.record(
        chat_id=12345,
        tx_type=TransactionType.DEPOSIT,
        amount_raw=100_000,
        raw_date="2026-09-20",
        account_id=accounts[1].id,
        user_id=user.user_id,
    )
    service.record(
        chat_id=12345,
        tx_type=TransactionType.WITHDRAWAL,
        amount_raw=150_000,
        raw_date="2026-09-20",
        account_id=accounts[1].id,
        user_id=user.user_id,
    )

    # 3. Check Daily Summary:
    # Total Deposit = 50_000 + 100_000 = 150_000
    # Total Withdrawal = 20_000 + 150_000 = 170_000
    # Total Net Profit = 170_000 - 150_000 = +20_000
    daily = service.get_daily_summary(
        chat_id=12345, target_date_str="2026-09-20", user_id=user.user_id
    )
    assert daily.total_deposit_cents == 150_000
    assert daily.total_withdrawal_cents == 170_000
    assert daily.net_profit_cents == 20_000

    # Check that account breakdowns match each account
    ab_map = {ab.account_id: ab for ab in daily.account_breakdowns}
    assert accounts[0].id in ab_map
    assert ab_map[accounts[0].id].deposit_cents == 50_000
    assert ab_map[accounts[0].id].withdrawal_cents == 20_000
    assert ab_map[accounts[0].id].net_profit_cents == -30_000

    assert accounts[1].id in ab_map
    assert ab_map[accounts[1].id].deposit_cents == 100_000
    assert ab_map[accounts[1].id].withdrawal_cents == 150_000
    assert ab_map[accounts[1].id].net_profit_cents == 50_000

    # 4. Check account balance:
    # Source B 1 initial: 100_000. Deposits: 50_000, Withdrawals: 20_000 -> Current: 130_000
    source_b1_updated = service.get_account(user_id=user.user_id, account_id=accounts[0].id)
    assert source_b1_updated is not None
    assert source_b1_updated.current_balance_cents == 130_000
    assert source_b1_updated.net_profit_cents == -30_000


def test_resolve_account_from_text(tmp_path: Path) -> None:
    from surebet.storage.auth import AuthRepository

    db = tmp_path / "acc.db"
    init_db(db)
    service = AccountingService(AccountingRepository(db))

    auth = AuthRepository(db)
    user, _ = auth.register_user("bankroll-user2", "correct-horse-battery-staple")

    service.create_account(user_id=user.user_id, account_name="Source B 1")
    service.create_account(user_id=user.user_id, account_name="Source B 2")
    service.create_account(user_id=user.user_id, account_name="Source B VIP")

    # Exact, normalized, prefix, and fuzzy matching
    acc1 = service.resolve_account(user_id=user.user_id, text="source_b 1")
    assert acc1 is not None
    assert acc1.account_name == "Source B 1"

    acc1_dense = service.resolve_account(user_id=user.user_id, text="source_b1")
    assert acc1_dense is not None
    assert acc1_dense.account_name == "Source B 1"

    acc_vip = service.resolve_account(user_id=user.user_id, text="vip")
    assert acc_vip is not None
    assert acc_vip.account_name == "Source B VIP"

    assert service.resolve_account(user_id=user.user_id, text="olmayan_hesap") is None

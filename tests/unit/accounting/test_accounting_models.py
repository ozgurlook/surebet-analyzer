from datetime import UTC, datetime

from surebet.accounting.models import (
    AccountBreakdown,
    AccountingTransaction,
    BankrollAccount,
    DailySummary,
    DayBreakdown,
    TransactionStatus,
    TransactionType,
    WeeklySummary,
)


def test_accounting_transaction_model() -> None:
    now = datetime.now(UTC)
    tx = AccountingTransaction(
        id=1,
        chat_id=12345,
        tx_type=TransactionType.DEPOSIT,
        amount_cents=500_000,
        tx_date="2026-09-19",
        note="Test deposit",
        status=TransactionStatus.ACTIVE,
        created_at=now,
    )
    assert tx.id == 1
    assert tx.tx_type == TransactionType.DEPOSIT
    assert tx.amount_cents == 500_000
    assert tx.status == TransactionStatus.ACTIVE
    assert tx.cancelled_at is None


def test_daily_and_weekly_summary_models() -> None:
    daily = DailySummary(
        date_str="2026-09-19",
        total_deposit_cents=100_000,
        total_withdrawal_cents=150_000,
        net_profit_cents=50_000,
        transaction_count=2,
        transactions=[],
    )
    assert daily.net_profit_cents == 50_000
    assert daily.account_breakdowns == []

    breakdown = DayBreakdown(
        date_str="2026-09-14",
        day_name="Pazartesi",
        deposit_cents=100_000,
        withdrawal_cents=150_000,
        net_profit_cents=50_000,
        has_transactions=True,
    )
    assert breakdown.day_name == "Pazartesi"

    weekly = WeeklySummary(
        week_start_str="2026-09-14",
        week_end_str="2026-09-20",
        total_deposit_cents=100_000,
        total_withdrawal_cents=150_000,
        net_profit_cents=50_000,
        daily_breakdown=[breakdown],
    )
    assert weekly.week_start_str == "2026-09-14"
    assert len(weekly.daily_breakdown) == 1
    assert weekly.account_breakdowns == []


def test_bankroll_account_model() -> None:
    now = datetime.now(UTC)
    acc = BankrollAccount(
        id=1,
        user_id=10,
        account_name="Source B 1",
        initial_balance_cents=1_000_000,
        current_balance_cents=1_200_000,
        total_deposit_cents=500_000,
        total_withdrawal_cents=700_000,
        net_profit_cents=200_000,
        note="Ana hesap",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    assert acc.account_name == "Source B 1"
    assert acc.current_balance_cents == 1_200_000
    assert acc.net_profit_cents == 200_000

    tx = AccountingTransaction(
        id=2,
        chat_id=12345,
        tx_type=TransactionType.DEPOSIT,
        amount_cents=500_000,
        tx_date="2026-09-19",
        note="Source B 1 deposit",
        status=TransactionStatus.ACTIVE,
        created_at=now,
        account_id=1,
        user_id=10,
    )
    assert tx.account_id == 1
    assert tx.user_id == 10

    ab = AccountBreakdown(
        account_id=1,
        account_name="Source B 1",
        deposit_cents=500_000,
        withdrawal_cents=700_000,
        net_profit_cents=200_000,
    )
    assert ab.account_id == 1
    assert ab.net_profit_cents == 200_000

import sqlite3
from pathlib import Path

import pytest

from surebet.accounting.models import TransactionStatus, TransactionType
from surebet.accounting.storage import AccountingRepository
from surebet.storage.db import init_db


@pytest.fixture
def repo(tmp_path: Path) -> AccountingRepository:
    db_file = tmp_path / "test_accounting.db"
    init_db(db_file)
    return AccountingRepository(db_file)


def test_insert_and_get_transaction(repo: AccountingRepository) -> None:
    tx = repo.insert_transaction(
        chat_id=1001,
        tx_type=TransactionType.DEPOSIT,
        amount_cents=500_000,
        tx_date="2026-09-19",
        note="İlk kasa",
    )
    assert tx.id is not None
    assert tx.chat_id == 1001
    assert tx.amount_cents == 500_000
    assert tx.tx_type == TransactionType.DEPOSIT
    assert tx.tx_date == "2026-09-19"
    assert tx.note == "İlk kasa"
    assert tx.status == TransactionStatus.ACTIVE
    assert tx.created_at is not None
    assert tx.cancelled_at is None

    fetched = repo.get_transaction(chat_id=1001, tx_id=tx.id)
    assert fetched is not None
    assert fetched.id == tx.id
    assert fetched.amount_cents == 500_000
    assert fetched.note == "İlk kasa"


def test_cancel_transaction(repo: AccountingRepository) -> None:
    tx = repo.insert_transaction(
        chat_id=1001,
        tx_type=TransactionType.WITHDRAWAL,
        amount_cents=200_000,
        tx_date="2026-09-19",
    )
    cancelled = repo.cancel_transaction(chat_id=1001, tx_id=tx.id)
    assert cancelled is not None
    assert cancelled.status == TransactionStatus.CANCELLED
    assert cancelled.cancelled_at is not None

    # Cannot cancel again
    assert repo.cancel_transaction(chat_id=1001, tx_id=tx.id) is None
    # Cannot cancel other chat's transaction
    assert repo.cancel_transaction(chat_id=9999, tx_id=tx.id) is None


def test_date_range_and_recent(repo: AccountingRepository) -> None:
    repo.insert_transaction(1001, TransactionType.DEPOSIT, 100_000, "2026-09-17")
    repo.insert_transaction(1001, TransactionType.DEPOSIT, 200_000, "2026-09-18")
    t3 = repo.insert_transaction(1001, TransactionType.WITHDRAWAL, 300_000, "2026-09-19")
    repo.cancel_transaction(1001, t3.id)

    # Active only for date range
    active_range = repo.get_transactions_for_date_range(1001, "2026-09-17", "2026-09-19")
    assert len(active_range) == 2
    assert active_range[0].amount_cents == 100_000
    assert active_range[1].amount_cents == 200_000

    # Recent (includes all recent entries)
    recent = repo.list_recent_transactions(1001, limit=5)
    assert len(recent) == 3


def test_get_latest_transaction(repo: AccountingRepository) -> None:
    assert repo.get_latest_transaction(1001) is None

    t1 = repo.insert_transaction(1001, TransactionType.DEPOSIT, 100_000, "2026-09-18")
    t2 = repo.insert_transaction(1001, TransactionType.WITHDRAWAL, 200_000, "2026-09-19")

    latest = repo.get_latest_transaction(1001)
    assert latest is not None
    assert latest.id == t2.id

    # After cancelling t2, latest active should be t1
    repo.cancel_transaction(1001, t2.id)
    latest_active = repo.get_latest_transaction(1001)
    assert latest_active is not None
    assert latest_active.id == t1.id

    # If asking for any status, latest should still be t2
    latest_any = repo.get_latest_transaction(1001, status=None)
    assert latest_any is not None
    assert latest_any.id == t2.id


def test_bankroll_accounts_storage(repo: AccountingRepository, tmp_path: Path) -> None:
    # Need a user in users table to satisfy foreign key
    from surebet.storage.auth import AuthRepository

    db_file = tmp_path / "test_accounting.db"
    auth = AuthRepository(db_file)
    user, _ = auth.register_user("testuser", "correct-horse-battery-staple")

    # 1. Create accounts
    acc1 = repo.create_account(
        user_id=user.user_id,
        account_name="Source B 1",
        initial_balance_cents=100_000,
        note="Birinci hesap",
    )
    assert acc1.id is not None
    assert acc1.account_name == "Source B 1"
    assert acc1.initial_balance_cents == 100_000
    assert acc1.current_balance_cents == 100_000
    assert acc1.total_deposit_cents == 0
    assert acc1.total_withdrawal_cents == 0
    assert acc1.net_profit_cents == 0

    acc2 = repo.create_account(
        user_id=user.user_id,
        account_name="Source B 2",
        initial_balance_cents=200_000,
    )
    assert acc2.id is not None

    # Cannot create duplicate account name for same user
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_account(user_id=user.user_id, account_name="Source B 1")

    # List accounts
    accounts = repo.list_accounts(user_id=user.user_id)
    assert len(accounts) == 2

    # 2. Insert transactions linked to account
    tx1 = repo.insert_transaction(
        chat_id=1001,
        tx_type=TransactionType.DEPOSIT,
        amount_cents=50_000,
        tx_date="2026-09-20",
        account_id=acc1.id,
        user_id=user.user_id,
    )
    assert tx1.account_id == acc1.id
    assert tx1.user_id == user.user_id

    tx2 = repo.insert_transaction(
        chat_id=1001,
        tx_type=TransactionType.WITHDRAWAL,
        amount_cents=20_000,
        tx_date="2026-09-20",
        account_id=acc1.id,
        user_id=user.user_id,
    )

    # Check updated balance for acc1:
    # initial(100k) + deposit(50k) - withdrawal(20k) = 130k
    # net_profit: withdrawal(20k) - deposit(50k) = -30k
    updated_acc1 = repo.get_account(user_id=user.user_id, account_id=acc1.id)
    assert updated_acc1 is not None
    assert updated_acc1.total_deposit_cents == 50_000
    assert updated_acc1.total_withdrawal_cents == 20_000
    assert updated_acc1.net_profit_cents == -30_000
    assert updated_acc1.current_balance_cents == 130_000

    # 3. Cancel transaction updates account balance
    repo.cancel_transaction(chat_id=1001, tx_id=tx2.id)
    after_cancel_acc1 = repo.get_account(user_id=user.user_id, account_id=acc1.id)
    assert after_cancel_acc1 is not None
    assert after_cancel_acc1.total_withdrawal_cents == 0
    assert after_cancel_acc1.current_balance_cents == 150_000

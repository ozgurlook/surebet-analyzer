"""SQLite storage repository for accounting transactions."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from surebet.accounting.models import (
    AccountingTransaction,
    BankrollAccount,
    TransactionStatus,
    TransactionType,
)
from surebet.storage.db import get_connection


def _row_to_transaction(row: sqlite3.Row) -> AccountingTransaction:
    keys = row.keys()
    account_id = (
        int(row["account_id"]) if "account_id" in keys and row["account_id"] is not None else None
    )
    user_id = int(row["user_id"]) if "user_id" in keys and row["user_id"] is not None else None
    return AccountingTransaction(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        tx_type=TransactionType(row["tx_type"]),
        amount_cents=int(row["amount_cents"]),
        tx_date=str(row["tx_date"]),
        note=str(row["note"]) if row["note"] is not None else None,
        status=TransactionStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        cancelled_at=(
            datetime.fromisoformat(row["cancelled_at"]) if row["cancelled_at"] is not None else None
        ),
        account_id=account_id,
        user_id=user_id,
    )


def _row_to_account(row: sqlite3.Row) -> BankrollAccount:
    init_bal = int(row["initial_balance_cents"])
    keys = row.keys()
    total_dep = int(row["total_deposit_cents"]) if "total_deposit_cents" in keys else 0
    total_with = int(row["total_withdrawal_cents"]) if "total_withdrawal_cents" in keys else 0
    net_profit = total_with - total_dep
    curr_bal = init_bal + total_dep - total_with
    return BankrollAccount(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        account_name=str(row["account_name"]),
        initial_balance_cents=init_bal,
        current_balance_cents=curr_bal,
        total_deposit_cents=total_dep,
        total_withdrawal_cents=total_with,
        net_profit_cents=net_profit,
        note=str(row["note"]) if row["note"] is not None else None,
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class AccountingRepository:
    """Repository for storing and querying accounting cashflow transactions and accounts."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    # --- Bankroll Accounts CRUD ---

    def create_account(
        self,
        user_id: int,
        account_name: str,
        initial_balance_cents: int = 0,
        note: str | None = None,
    ) -> BankrollAccount:
        """Create a new Source B bankroll account."""
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO bankroll_accounts "
                "(user_id, account_name, initial_balance_cents, note, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (user_id, account_name.strip(), initial_balance_cents, note, now_iso, now_iso),
            )
            acc_id = cursor.lastrowid
        if acc_id is None:
            raise RuntimeError("Failed to insert bankroll account")
        res = self.get_account(user_id=user_id, account_id=acc_id)
        if res is None:
            raise RuntimeError("Failed to retrieve created bankroll account")
        return res

    def get_account(self, user_id: int, account_id: int) -> BankrollAccount | None:
        """Retrieve an account with live calculated active balances."""
        sql = """
            SELECT 
                a.*,
                COALESCE(SUM(CASE WHEN t.tx_type = 'DEPOSIT' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_deposit_cents,
                COALESCE(SUM(CASE WHEN t.tx_type = 'WITHDRAWAL' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_withdrawal_cents
            FROM bankroll_accounts a
            LEFT JOIN accounting_transactions t ON t.account_id = a.id
            WHERE a.id = ? AND a.user_id = ?
            GROUP BY a.id
        """
        with get_connection(self._db_path) as conn:
            row = conn.execute(sql, (account_id, user_id)).fetchone()
            if row is None:
                return None
            return _row_to_account(row)

    def get_account_by_name(self, user_id: int, account_name: str) -> BankrollAccount | None:
        """Retrieve an account by name (case-insensitive) for a user."""
        sql = """
            SELECT 
                a.*,
                COALESCE(SUM(CASE WHEN t.tx_type = 'DEPOSIT' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_deposit_cents,
                COALESCE(SUM(CASE WHEN t.tx_type = 'WITHDRAWAL' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_withdrawal_cents
            FROM bankroll_accounts a
            LEFT JOIN accounting_transactions t ON t.account_id = a.id
            WHERE LOWER(a.account_name) = LOWER(?) AND a.user_id = ? AND a.is_active = 1
            GROUP BY a.id
        """
        with get_connection(self._db_path) as conn:
            row = conn.execute(sql, (account_name.strip(), user_id)).fetchone()
            if row is None:
                return None
            return _row_to_account(row)

    def list_accounts(self, user_id: int, include_inactive: bool = False) -> list[BankrollAccount]:
        """List accounts for a user with live calculated active balances."""
        active_filter = "" if include_inactive else "AND a.is_active = 1"
        sql = f"""
            SELECT 
                a.*,
                COALESCE(SUM(CASE WHEN t.tx_type = 'DEPOSIT' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_deposit_cents,
                COALESCE(SUM(CASE WHEN t.tx_type = 'WITHDRAWAL' AND t.status = 'ACTIVE' THEN t.amount_cents ELSE 0 END), 0) AS total_withdrawal_cents
            FROM bankroll_accounts a
            LEFT JOIN accounting_transactions t ON t.account_id = a.id
            WHERE a.user_id = ? {active_filter}
            GROUP BY a.id
            ORDER BY a.id ASC
        """
        with get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (user_id,)).fetchall()
            return [_row_to_account(row) for row in rows]

    def update_account(
        self,
        user_id: int,
        account_id: int,
        account_name: str | None = None,
        initial_balance_cents: int | None = None,
        note: str | None = None,
        is_active: bool | None = None,
    ) -> BankrollAccount | None:
        """Update an existing bankroll account."""
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            # Check existence
            existing = conn.execute(
                "SELECT * FROM bankroll_accounts WHERE id = ? AND user_id = ?",
                (account_id, user_id),
            ).fetchone()
            if existing is None:
                return None

            updates: list[str] = ["updated_at = ?"]
            params: list[object] = [now_iso]

            if account_name is not None:
                updates.append("account_name = ?")
                params.append(account_name.strip())
            if initial_balance_cents is not None:
                updates.append("initial_balance_cents = ?")
                params.append(initial_balance_cents)
            if note is not None:
                updates.append("note = ?")
                params.append(note)
            if is_active is not None:
                updates.append("is_active = ?")
                params.append(1 if is_active else 0)

            params.extend([account_id, user_id])
            conn.execute(
                f"UPDATE bankroll_accounts SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params,
            )

        return self.get_account(user_id=user_id, account_id=account_id)

    def delete_account(self, user_id: int, account_id: int) -> bool:
        """Soft-delete (archive) an account."""
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "UPDATE bankroll_accounts SET is_active = 0, updated_at = ? WHERE id = ? AND user_id = ?",
                (now_iso, account_id, user_id),
            )
            return cursor.rowcount > 0

    # --- Transactions CRUD ---

    def insert_transaction(
        self,
        chat_id: int,
        tx_type: TransactionType,
        amount_cents: int,
        tx_date: str,
        note: str | None = None,
        account_id: int | None = None,
        user_id: int | None = None,
    ) -> AccountingTransaction:
        """Insert a new accounting transaction."""
        now_iso = datetime.now(UTC).isoformat()
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO accounting_transactions "
                "(chat_id, tx_type, amount_cents, tx_date, note, status, created_at, cancelled_at, account_id, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    tx_type.value,
                    amount_cents,
                    tx_date,
                    note,
                    TransactionStatus.ACTIVE.value,
                    now_iso,
                    None,
                    account_id,
                    user_id,
                ),
            )
            tx_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM accounting_transactions WHERE id = ?",
                (tx_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to retrieve inserted transaction")
            return _row_to_transaction(row)

    def cancel_transaction(
        self,
        chat_id: int | None = None,
        tx_id: int = 0,
        user_id: int | None = None,
    ) -> AccountingTransaction | None:
        """Cancel an active transaction for the specified chat_id or user_id (audit soft delete)."""
        now_iso = datetime.now(UTC).isoformat()
        owner_clause = ""
        owner_param: list[object] = []
        if user_id is not None:
            owner_clause = " AND user_id = ?"
            owner_param.append(user_id)
        elif chat_id is not None:
            owner_clause = " AND chat_id = ?"
            owner_param.append(chat_id)

        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                f"UPDATE accounting_transactions "
                f"SET status = ?, cancelled_at = ? "
                f"WHERE id = ? AND status = ?{owner_clause}",
                [
                    TransactionStatus.CANCELLED.value,
                    now_iso,
                    tx_id,
                    TransactionStatus.ACTIVE.value,
                    *owner_param,
                ],
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM accounting_transactions WHERE id = ?",
                (tx_id,),
            ).fetchone()
            if row is None:
                return None
            return _row_to_transaction(row)

    def get_transaction(self, chat_id: int, tx_id: int) -> AccountingTransaction | None:
        """Retrieve a specific transaction by id and chat_id."""
        with get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM accounting_transactions WHERE chat_id = ? AND id = ?",
                (chat_id, tx_id),
            ).fetchone()
            if row is None:
                return None
            return _row_to_transaction(row)

    def get_latest_transaction(
        self, chat_id: int, status: TransactionStatus | None = TransactionStatus.ACTIVE
    ) -> AccountingTransaction | None:
        """Retrieve the latest transaction for a chat, optionally filtering by status."""
        with get_connection(self._db_path) as conn:
            if status is not None:
                row = conn.execute(
                    "SELECT * FROM accounting_transactions "
                    "WHERE chat_id = ? AND status = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (chat_id, status.value),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM accounting_transactions "
                    "WHERE chat_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (chat_id,),
                ).fetchone()
            if row is None:
                return None
            return _row_to_transaction(row)

    def list_recent_transactions(
        self,
        chat_id: int | None = None,
        limit: int = 5,
        user_id: int | None = None,
        account_id: int | None = None,
    ) -> list[AccountingTransaction]:
        """List the most recent transactions, optionally filtering by user_id/chat_id and account_id."""
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        elif chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)

        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        sql = f"SELECT * FROM accounting_transactions {where_sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        with get_connection(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_transaction(row) for row in rows]

    def get_transactions_for_date_range(
        self,
        chat_id: int | None,
        start_date: str,
        end_date: str,
        status: TransactionStatus | None = TransactionStatus.ACTIVE,
        user_id: int | None = None,
        account_id: int | None = None,
    ) -> list[AccountingTransaction]:
        """Retrieve transactions within a date range [start_date, end_date]."""
        clauses: list[str] = ["tx_date >= ?", "tx_date <= ?"]
        params: list[object] = [start_date, end_date]

        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        elif chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)

        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        sql = f"SELECT * FROM accounting_transactions WHERE {' AND '.join(clauses)} ORDER BY tx_date ASC, created_at ASC, id ASC"
        with get_connection(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_transaction(row) for row in rows]

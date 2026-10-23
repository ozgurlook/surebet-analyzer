"""Service and business logic for daily accounting, date/amount parsing, and summaries."""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

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
from surebet.accounting.storage import AccountingRepository
from surebet.money import decimal_to_cents

ISTANBUL = ZoneInfo("Europe/Istanbul")
TURKISH_DAY_NAMES = [
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
]


def parse_amount_to_cents(raw: str | int) -> int:
    """Parse a user-provided amount string or integer into integer cents.

    Supports inputs such as:
      - "5000", "5.000", "5000 TL", "5000 tl", "₺5000" -> 500_000 cents
      - "1250,50", "1250.50" -> 125_050 cents
      - "10k", "10K" -> 1_000_000 cents
      - "2.5k", "2,5k" -> 250_000 cents
    """
    if isinstance(raw, bool):
        raise TypeError("raw cannot be a boolean")
    if isinstance(raw, int):
        if raw <= 0:
            raise ValueError("Amount must be strictly positive")
        return raw * 100

    if not isinstance(raw, str):
        raise TypeError("raw must be a string or integer")

    s = raw.strip().lower()
    if not s:
        raise ValueError("Amount string cannot be empty")

    # Strip currency symbols and names
    s = re.sub(r"^(?:tl|try|₺|\$|€)\s*", "", s)
    s = re.sub(r"\s*(?:tl|try|₺|türk lirası|turk lirasi)$", "", s)
    s = s.strip()
    if not s:
        raise ValueError(f"Invalid amount format: {raw}")

    # Check for multiplier suffix: 'k' or 'bin'
    multiplier = Decimal(1)
    if s.endswith("k"):
        s = s[:-1].strip()
        multiplier = Decimal(1000)
    elif s.endswith("bin"):
        s = s[:-3].strip()
        multiplier = Decimal(1000)

    # Normalize separators
    if "." in s and "," in s:
        dot_idx = s.rfind(".")
        comma_idx = s.rfind(",")
        if dot_idx < comma_idx:
            # 1.250,50 -> dot is thousand, comma is decimal
            s = s.replace(".", "").replace(",", ".")
        else:
            # 1,250.50 -> comma is thousand, dot is decimal
            s = s.replace(",", "")
    elif "," in s:
        comma_parts = s.split(",")
        if len(comma_parts) > 2:
            # multiple commas: 1,000,000 -> thousands separator
            s = s.replace(",", "")
        elif len(comma_parts) == 2 and len(comma_parts[1]) == 3 and multiplier == Decimal(1):
            # 5,000 -> thousands separator
            s = s.replace(",", "")
        else:
            # 1250,50 or 2,5k -> decimal separator
            s = s.replace(",", ".")
    elif "." in s:
        dot_parts = s.split(".")
        if len(dot_parts) > 2:
            # multiple dots: 1.000.000 -> thousands separator
            s = s.replace(".", "")
        elif len(dot_parts) == 2 and len(dot_parts[1]) == 3 and multiplier == Decimal(1):
            # 5.000 -> thousands separator
            s = s.replace(".", "")
        else:
            # 1250.50 or 2.5k -> decimal separator
            pass

    try:
        amount_dec = Decimal(s) * multiplier
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid amount: {raw}") from e

    if amount_dec <= 0:
        raise ValueError("Amount must be strictly positive")

    return decimal_to_cents(amount_dec)


def parse_date_input(
    raw: str | None = None,
    now: datetime | None = None,
) -> str:
    """Parse a flexible user date input into an ISO date string (YYYY-MM-DD).

    Uses the Europe/Istanbul timezone by default.
    Supports:
      - None, "", "bugun", "bugün" -> current local date
      - "dun", "dün" -> yesterday
      - "DD.MM", "DD/MM" -> smart year resolution with new-year boundary
      - "DD.MM.YYYY", "DD/MM/YYYY", "YYYY-MM-DD" -> explicit year
    """
    if now is None:
        now_dt = datetime.now(ISTANBUL)
    elif now.tzinfo is None:
        now_dt = now.replace(tzinfo=ISTANBUL)
    else:
        now_dt = now.astimezone(ISTANBUL)

    today = now_dt.date()

    if raw is None:
        return today.isoformat()

    cleaned = raw.strip().lower()
    if not cleaned or cleaned in ("bugun", "bugün"):
        return today.isoformat()
    if cleaned in ("dun", "dün"):
        return (today - timedelta(days=1)).isoformat()

    parts = re.split(r"[-/.]", cleaned)
    if len(parts) == 2:
        try:
            day = int(parts[0])
            month = int(parts[1])
        except ValueError as e:
            raise ValueError(f"Invalid date: {raw}") from e

        year = today.year
        try:
            candidate = date(year, month, day)
        except ValueError as e:
            raise ValueError(f"Invalid date: {raw}") from e

        # New-year boundary condition:
        # e.g., on 2027-01-02, entering "31.12" should resolve to 2026-12-31
        if candidate > today + timedelta(days=30):
            try:
                candidate = date(year - 1, month, day)
            except ValueError as e:
                raise ValueError(f"Invalid date: {raw}") from e

        return candidate.isoformat()

    if len(parts) == 3:
        try:
            p0, p1, p2 = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError as e:
            raise ValueError(f"Invalid date: {raw}") from e

        if len(parts[0]) == 4:
            # ISO format: YYYY-MM-DD
            year, month, day = p0, p1, p2
        elif len(parts[2]) in (2, 4):
            # DD.MM.YYYY or DD.MM.YY
            day, month, year = p0, p1, p2
            if year < 100:
                year += 2000
        else:
            raise ValueError(f"Invalid date format: {raw}")

        try:
            d = date(year, month, day)
        except ValueError as e:
            raise ValueError(f"Invalid date: {raw}") from e

        return d.isoformat()

    raise ValueError(f"Invalid date format: {raw}")


class AccountingService:
    """Business logic for transactions, bankroll accounts, daily summaries, and weekly reporting."""

    def __init__(self, repo: AccountingRepository) -> None:
        self._repo = repo

    # --- Bankroll Account Operations ---

    def create_account(
        self,
        user_id: int,
        account_name: str,
        initial_balance_cents: int = 0,
        note: str | None = None,
    ) -> BankrollAccount:
        """Create a new Source B bankroll account."""
        return self._repo.create_account(
            user_id=user_id,
            account_name=account_name,
            initial_balance_cents=initial_balance_cents,
            note=note,
        )

    def get_account(self, user_id: int, account_id: int) -> BankrollAccount | None:
        """Retrieve an account with live balance calculation."""
        return self._repo.get_account(user_id=user_id, account_id=account_id)

    def get_account_by_name(self, user_id: int, account_name: str) -> BankrollAccount | None:
        """Retrieve an account by name (case-insensitive) for a user."""
        return self._repo.get_account_by_name(user_id=user_id, account_name=account_name)

    def list_accounts(self, user_id: int, include_inactive: bool = False) -> list[BankrollAccount]:
        """List all accounts for a user with calculated balances."""
        return self._repo.list_accounts(user_id=user_id, include_inactive=include_inactive)

    def update_account(
        self,
        user_id: int,
        account_id: int,
        account_name: str | None = None,
        initial_balance_cents: int | None = None,
        note: str | None = None,
        is_active: bool | None = None,
    ) -> BankrollAccount | None:
        """Update an account's name, initial balance, note, or active status."""
        return self._repo.update_account(
            user_id=user_id,
            account_id=account_id,
            account_name=account_name,
            initial_balance_cents=initial_balance_cents,
            note=note,
            is_active=is_active,
        )

    def delete_account(self, user_id: int, account_id: int) -> bool:
        """Soft-delete an account."""
        return self._repo.delete_account(user_id=user_id, account_id=account_id)

    def resolve_account(self, user_id: int, text: str) -> BankrollAccount | None:
        """Resolve an account by matching text against the user's active accounts.

        Tries in order:
        1. Exact match (case-insensitive)
        2. Dense match (ignoring whitespace and punctuation)
        3. Word match (e.g. 'vip' matches 'Source B VIP')
        4. Substring match
        """
        raw = text.strip().lower()
        if not raw:
            return None

        accounts = self._repo.list_accounts(user_id=user_id, include_inactive=False)
        if not accounts:
            return None

        # 1. Exact match
        for acc in accounts:
            if acc.account_name.strip().lower() == raw:
                return acc

        # 2. Dense match (strip whitespace, hyphens, underscores)
        def dense(s: str) -> str:
            return re.sub(r"[\s\-_]+", "", s.lower())

        dense_raw = dense(raw)
        for acc in accounts:
            if dense(acc.account_name) == dense_raw:
                return acc

        # 3. Word match (if raw is one of the tokens in account_name)
        for acc in accounts:
            tokens = re.split(r"[\s\-_]+", acc.account_name.lower())
            if raw in tokens:
                return acc

        # 4. Substring match
        for acc in accounts:
            acc_dense = dense(acc.account_name)
            if dense_raw in acc_dense or acc_dense in dense_raw:
                return acc

        return None

    # --- Transaction Operations ---

    def record(
        self,
        chat_id: int,
        tx_type: TransactionType,
        amount: str | int | None = None,
        raw_date: str | None = None,
        note: str | None = None,
        account_id: int | None = None,
        user_id: int | None = None,
        amount_raw: str | int | None = None,
    ) -> AccountingTransaction:
        """Record a new deposit or withdrawal transaction."""
        amt = amount if amount is not None else amount_raw
        if amt is None:
            raise ValueError("Amount must be provided")

        if isinstance(amt, int):
            amount_cents = amt
        elif isinstance(amt, str):
            amount_cents = parse_amount_to_cents(amt)
        else:
            raise TypeError("amount must be an int (cents) or str")

        if amount_cents <= 0:
            raise ValueError("Amount must be strictly positive")

        tx_date = parse_date_input(raw_date)
        return self._repo.insert_transaction(
            chat_id=chat_id,
            tx_type=tx_type,
            amount_cents=amount_cents,
            tx_date=tx_date,
            note=note,
            account_id=account_id,
            user_id=user_id,
        )

    def cancel(
        self,
        chat_id: int | None = None,
        tx_id: int = 0,
        user_id: int | None = None,
    ) -> AccountingTransaction | None:
        """Cancel a specific active transaction."""
        return self._repo.cancel_transaction(chat_id=chat_id, tx_id=tx_id, user_id=user_id)

    def cancel_latest(self, chat_id: int) -> AccountingTransaction | None:
        """Cancel the latest active transaction for the chat."""
        latest = self._repo.get_latest_transaction(chat_id=chat_id, status=TransactionStatus.ACTIVE)
        if latest is None:
            return None
        return self._repo.cancel_transaction(chat_id=chat_id, tx_id=latest.id)

    def get_daily_summary(
        self,
        chat_id: int | None = None,
        target_date_str: str | None = None,
        user_id: int | None = None,
        account_id: int | None = None,
    ) -> DailySummary:
        """Calculate the daily summary for a target date (defaults to today)."""
        date_str = parse_date_input(target_date_str)
        transactions = self._repo.get_transactions_for_date_range(
            chat_id=chat_id,
            start_date=date_str,
            end_date=date_str,
            status=TransactionStatus.ACTIVE,
            user_id=user_id,
            account_id=account_id,
        )

        total_deposit_cents = sum(
            tx.amount_cents for tx in transactions if tx.tx_type == TransactionType.DEPOSIT
        )
        total_withdrawal_cents = sum(
            tx.amount_cents for tx in transactions if tx.tx_type == TransactionType.WITHDRAWAL
        )
        net_profit_cents = total_withdrawal_cents - total_deposit_cents

        # Build account breakdowns
        user_accounts: dict[int, str] = {}
        if user_id is not None:
            user_accounts = {
                a.id: a.account_name
                for a in self._repo.list_accounts(user_id=user_id, include_inactive=True)
            }

        txs_by_account: dict[int, list[AccountingTransaction]] = defaultdict(list)
        for tx in transactions:
            if tx.account_id is not None:
                txs_by_account[tx.account_id].append(tx)

        account_breakdowns: list[AccountBreakdown] = []
        for acc_id, acc_txs in txs_by_account.items():
            acc_name = user_accounts.get(acc_id, f"Hesap #{acc_id}")
            acc_dep = sum(t.amount_cents for t in acc_txs if t.tx_type == TransactionType.DEPOSIT)
            acc_with = sum(
                t.amount_cents for t in acc_txs if t.tx_type == TransactionType.WITHDRAWAL
            )
            account_breakdowns.append(
                AccountBreakdown(
                    account_id=acc_id,
                    account_name=acc_name,
                    deposit_cents=acc_dep,
                    withdrawal_cents=acc_with,
                    net_profit_cents=acc_with - acc_dep,
                )
            )
        account_breakdowns.sort(key=lambda x: x.account_id)

        return DailySummary(
            date_str=date_str,
            total_deposit_cents=total_deposit_cents,
            total_withdrawal_cents=total_withdrawal_cents,
            net_profit_cents=net_profit_cents,
            transaction_count=len(transactions),
            transactions=transactions,
            account_breakdowns=account_breakdowns,
        )

    def get_weekly_summary(
        self,
        chat_id: int | None = None,
        target_date_str: str | None = None,
        user_id: int | None = None,
        account_id: int | None = None,
    ) -> WeeklySummary:
        """Calculate the 7-day Monday-to-Sunday weekly summary containing the target date."""
        ref_date_str = parse_date_input(target_date_str)
        ref_date = date.fromisoformat(ref_date_str)

        # Monday is 0, Sunday is 6
        monday = ref_date - timedelta(days=ref_date.weekday())
        sunday = monday + timedelta(days=6)
        week_start_str = monday.isoformat()
        week_end_str = sunday.isoformat()

        transactions = self._repo.get_transactions_for_date_range(
            chat_id=chat_id,
            start_date=week_start_str,
            end_date=week_end_str,
            status=TransactionStatus.ACTIVE,
            user_id=user_id,
            account_id=account_id,
        )

        txs_by_date: dict[str, list[AccountingTransaction]] = defaultdict(list)
        for tx in transactions:
            txs_by_date[tx.tx_date].append(tx)

        daily_breakdown: list[DayBreakdown] = []
        total_deposit_cents = 0
        total_withdrawal_cents = 0

        for i in range(7):
            day_date = monday + timedelta(days=i)
            day_str = day_date.isoformat()
            day_txs = txs_by_date.get(day_str, [])

            day_deposit = sum(
                tx.amount_cents for tx in day_txs if tx.tx_type == TransactionType.DEPOSIT
            )
            day_withdrawal = sum(
                tx.amount_cents for tx in day_txs if tx.tx_type == TransactionType.WITHDRAWAL
            )
            day_net = day_withdrawal - day_deposit

            total_deposit_cents += day_deposit
            total_withdrawal_cents += day_withdrawal

            daily_breakdown.append(
                DayBreakdown(
                    date_str=day_str,
                    day_name=TURKISH_DAY_NAMES[i],
                    deposit_cents=day_deposit,
                    withdrawal_cents=day_withdrawal,
                    net_profit_cents=day_net,
                    has_transactions=len(day_txs) > 0,
                )
            )

        net_profit_cents = total_withdrawal_cents - total_deposit_cents

        # Build weekly account breakdowns
        user_accounts: dict[int, str] = {}
        if user_id is not None:
            user_accounts = {
                a.id: a.account_name
                for a in self._repo.list_accounts(user_id=user_id, include_inactive=True)
            }

        txs_by_account: dict[int, list[AccountingTransaction]] = defaultdict(list)
        for tx in transactions:
            if tx.account_id is not None:
                txs_by_account[tx.account_id].append(tx)

        account_breakdowns: list[AccountBreakdown] = []
        for acc_id, acc_txs in txs_by_account.items():
            acc_name = user_accounts.get(acc_id, f"Hesap #{acc_id}")
            acc_dep = sum(t.amount_cents for t in acc_txs if t.tx_type == TransactionType.DEPOSIT)
            acc_with = sum(
                t.amount_cents for t in acc_txs if t.tx_type == TransactionType.WITHDRAWAL
            )
            account_breakdowns.append(
                AccountBreakdown(
                    account_id=acc_id,
                    account_name=acc_name,
                    deposit_cents=acc_dep,
                    withdrawal_cents=acc_with,
                    net_profit_cents=acc_with - acc_dep,
                )
            )
        account_breakdowns.sort(key=lambda x: x.account_id)

        return WeeklySummary(
            week_start_str=week_start_str,
            week_end_str=week_end_str,
            total_deposit_cents=total_deposit_cents,
            total_withdrawal_cents=total_withdrawal_cents,
            net_profit_cents=net_profit_cents,
            daily_breakdown=daily_breakdown,
            account_breakdowns=account_breakdowns,
        )

    def get_recent(
        self,
        chat_id: int | None = None,
        limit: int = 5,
        user_id: int | None = None,
        account_id: int | None = None,
    ) -> list[AccountingTransaction]:
        """List the most recent transactions."""
        return self._repo.list_recent_transactions(
            chat_id=chat_id, limit=limit, user_id=user_id, account_id=account_id
        )

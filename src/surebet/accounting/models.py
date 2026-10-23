"""Domain data models for daily cash flow accounting."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TransactionType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"


class TransactionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class BankrollAccount:
    id: int
    user_id: int
    account_name: str
    initial_balance_cents: int
    current_balance_cents: int
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    note: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AccountBreakdown:
    account_id: int
    account_name: str
    deposit_cents: int
    withdrawal_cents: int
    net_profit_cents: int


@dataclass(frozen=True)
class AccountingTransaction:
    id: int
    chat_id: int
    tx_type: TransactionType
    amount_cents: int
    tx_date: str  # YYYY-MM-DD
    note: str | None
    status: TransactionStatus
    created_at: datetime
    cancelled_at: datetime | None = None
    account_id: int | None = None
    user_id: int | None = None


@dataclass(frozen=True)
class DailySummary:
    date_str: str  # YYYY-MM-DD
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    transaction_count: int
    transactions: list[AccountingTransaction]
    account_breakdowns: list[AccountBreakdown] = field(default_factory=list)


@dataclass(frozen=True)
class DayBreakdown:
    date_str: str  # YYYY-MM-DD
    day_name: str  # Pazartesi, Salı, vb.
    deposit_cents: int
    withdrawal_cents: int
    net_profit_cents: int
    has_transactions: bool


@dataclass(frozen=True)
class WeeklySummary:
    week_start_str: str  # YYYY-MM-DD
    week_end_str: str  # YYYY-MM-DD
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    daily_breakdown: list[DayBreakdown]
    account_breakdowns: list[AccountBreakdown] = field(default_factory=list)

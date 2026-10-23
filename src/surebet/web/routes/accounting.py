"""REST API endpoints for multi-account bankroll and accounting."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from surebet.accounting.models import BankrollAccount, TransactionType
from surebet.accounting.service import AccountingService
from surebet.storage.auth import SessionData
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(prefix="/accounting", tags=["accounting"])


# --- Pydantic Schemas ---


class BankrollAccountResponse(BaseModel):
    id: int
    user_id: int
    account_name: str
    initial_balance_cents: int
    current_balance_cents: int
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    note: str | None = None
    is_active: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, a: BankrollAccount) -> "BankrollAccountResponse":
        return cls(
            id=a.id,
            user_id=a.user_id,
            account_name=a.account_name,
            initial_balance_cents=a.initial_balance_cents,
            current_balance_cents=a.current_balance_cents,
            total_deposit_cents=a.total_deposit_cents,
            total_withdrawal_cents=a.total_withdrawal_cents,
            net_profit_cents=a.net_profit_cents,
            note=a.note,
            is_active=a.is_active,
            created_at=a.created_at.isoformat(),
            updated_at=a.updated_at.isoformat(),
        )


class CreateAccountRequest(BaseModel):
    account_name: str = Field(..., min_length=1, max_length=100)
    initial_balance_cents: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=500)


class UpdateAccountRequest(BaseModel):
    account_name: str | None = Field(default=None, min_length=1, max_length=100)
    initial_balance_cents: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class AccountingTransactionResponse(BaseModel):
    id: int
    chat_id: int
    tx_type: str
    amount_cents: int
    tx_date: str
    note: str | None = None
    status: str
    created_at: str
    cancelled_at: str | None = None
    account_id: int | None = None
    user_id: int | None = None


class RecordTransactionRequest(BaseModel):
    tx_type: TransactionType
    amount_cents: int = Field(..., gt=0)
    tx_date: str | None = None
    note: str | None = Field(default=None, max_length=500)
    account_id: int | None = None


class AccountBreakdownResponse(BaseModel):
    account_id: int
    account_name: str
    deposit_cents: int
    withdrawal_cents: int
    net_profit_cents: int


class DailySummaryResponse(BaseModel):
    date_str: str
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    transaction_count: int
    transactions: list[AccountingTransactionResponse]
    account_breakdowns: list[AccountBreakdownResponse]


class DayBreakdownResponse(BaseModel):
    date_str: str
    day_name: str
    deposit_cents: int
    withdrawal_cents: int
    net_profit_cents: int
    has_transactions: bool


class WeeklySummaryResponse(BaseModel):
    week_start_str: str
    week_end_str: str
    total_deposit_cents: int
    total_withdrawal_cents: int
    net_profit_cents: int
    daily_breakdown: list[DayBreakdownResponse]
    account_breakdowns: list[AccountBreakdownResponse]


class AccountingSummaryResponse(BaseModel):
    daily: DailySummaryResponse
    weekly: WeeklySummaryResponse


def _get_service(request: Request) -> AccountingService:
    service: AccountingService = request.app.state.accounting_service
    return service


# --- Account Endpoints ---


@router.get("/accounts", response_model=list[BankrollAccountResponse])
def list_accounts(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    include_inactive: bool = Query(default=False),
) -> list[BankrollAccountResponse]:
    """List bankroll accounts for the authenticated user."""
    service = _get_service(request)
    accounts = service.list_accounts(user_id=session.user_id, include_inactive=include_inactive)
    return [BankrollAccountResponse.from_domain(a) for a in accounts]


@router.post("/accounts", response_model=BankrollAccountResponse)
def create_account(
    req: CreateAccountRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> BankrollAccountResponse:
    """Create a new Source B bankroll account."""
    service = _get_service(request)
    name = req.account_name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account name cannot be blank",
        )

    # Check for duplicate
    existing = service.get_account_by_name(user_id=session.user_id, account_name=name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Account '{name}' already exists",
        )

    acc = service.create_account(
        user_id=session.user_id,
        account_name=name,
        initial_balance_cents=req.initial_balance_cents,
        note=req.note,
    )
    return BankrollAccountResponse.from_domain(acc)


@router.put("/accounts/{account_id}", response_model=BankrollAccountResponse)
def update_account(
    account_id: int,
    req: UpdateAccountRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> BankrollAccountResponse:
    """Update bankroll account details."""
    service = _get_service(request)
    updated = service.update_account(
        user_id=session.user_id,
        account_id=account_id,
        account_name=req.account_name,
        initial_balance_cents=req.initial_balance_cents,
        note=req.note,
        is_active=req.is_active,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )
    return BankrollAccountResponse.from_domain(updated)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> dict[str, bool]:
    """Archive / soft delete a bankroll account."""
    service = _get_service(request)
    success = service.delete_account(user_id=session.user_id, account_id=account_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )
    return {"ok": True}


# --- Summary & Reporting Endpoints ---


@router.get("/summary", response_model=AccountingSummaryResponse)
def get_summary(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    date: str | None = Query(default=None),
    account_id: int | None = Query(default=None),
) -> AccountingSummaryResponse:
    """Get daily and weekly accounting summaries with account breakdowns."""
    service = _get_service(request)
    daily = service.get_daily_summary(
        target_date_str=date,
        user_id=session.user_id,
        account_id=account_id,
    )
    weekly = service.get_weekly_summary(
        target_date_str=date,
        user_id=session.user_id,
        account_id=account_id,
    )

    daily_res = DailySummaryResponse(
        date_str=daily.date_str,
        total_deposit_cents=daily.total_deposit_cents,
        total_withdrawal_cents=daily.total_withdrawal_cents,
        net_profit_cents=daily.net_profit_cents,
        transaction_count=daily.transaction_count,
        transactions=[
            AccountingTransactionResponse(
                id=t.id,
                chat_id=t.chat_id,
                tx_type=t.tx_type.value,
                amount_cents=t.amount_cents,
                tx_date=t.tx_date,
                note=t.note,
                status=t.status.value,
                created_at=t.created_at.isoformat(),
                cancelled_at=t.cancelled_at.isoformat() if t.cancelled_at else None,
                account_id=t.account_id,
                user_id=t.user_id,
            )
            for t in daily.transactions
        ],
        account_breakdowns=[
            AccountBreakdownResponse(
                account_id=ab.account_id,
                account_name=ab.account_name,
                deposit_cents=ab.deposit_cents,
                withdrawal_cents=ab.withdrawal_cents,
                net_profit_cents=ab.net_profit_cents,
            )
            for ab in daily.account_breakdowns
        ],
    )

    weekly_res = WeeklySummaryResponse(
        week_start_str=weekly.week_start_str,
        week_end_str=weekly.week_end_str,
        total_deposit_cents=weekly.total_deposit_cents,
        total_withdrawal_cents=weekly.total_withdrawal_cents,
        net_profit_cents=weekly.net_profit_cents,
        daily_breakdown=[
            DayBreakdownResponse(
                date_str=db.date_str,
                day_name=db.day_name,
                deposit_cents=db.deposit_cents,
                withdrawal_cents=db.withdrawal_cents,
                net_profit_cents=db.net_profit_cents,
                has_transactions=db.has_transactions,
            )
            for db in weekly.daily_breakdown
        ],
        account_breakdowns=[
            AccountBreakdownResponse(
                account_id=ab.account_id,
                account_name=ab.account_name,
                deposit_cents=ab.deposit_cents,
                withdrawal_cents=ab.withdrawal_cents,
                net_profit_cents=ab.net_profit_cents,
            )
            for ab in weekly.account_breakdowns
        ],
    )

    return AccountingSummaryResponse(daily=daily_res, weekly=weekly_res)


# --- Transaction Endpoints ---


@router.get("/transactions", response_model=list[AccountingTransactionResponse])
def list_transactions(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    limit: int = Query(default=50, ge=1, le=200),
    account_id: int | None = Query(default=None),
) -> list[AccountingTransactionResponse]:
    """List recent accounting transactions."""
    service = _get_service(request)
    txs = service.get_recent(limit=limit, user_id=session.user_id, account_id=account_id)
    return [
        AccountingTransactionResponse(
            id=t.id,
            chat_id=t.chat_id,
            tx_type=t.tx_type.value,
            amount_cents=t.amount_cents,
            tx_date=t.tx_date,
            note=t.note,
            status=t.status.value,
            created_at=t.created_at.isoformat(),
            cancelled_at=t.cancelled_at.isoformat() if t.cancelled_at else None,
            account_id=t.account_id,
            user_id=t.user_id,
        )
        for t in txs
    ]


@router.post("/transactions", response_model=AccountingTransactionResponse)
def record_transaction(
    req: RecordTransactionRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> AccountingTransactionResponse:
    """Record a new deposit or withdrawal transaction for an account."""
    service = _get_service(request)

    # If account_id provided, ensure it belongs to session user
    if req.account_id is not None:
        account = service.get_account(user_id=session.user_id, account_id=req.account_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bankroll account not found",
            )

    tx = service.record(
        chat_id=0,
        tx_type=req.tx_type,
        amount=req.amount_cents,
        raw_date=req.tx_date,
        note=req.note,
        account_id=req.account_id,
        user_id=session.user_id,
    )

    return AccountingTransactionResponse(
        id=tx.id,
        chat_id=tx.chat_id,
        tx_type=tx.tx_type.value,
        amount_cents=tx.amount_cents,
        tx_date=tx.tx_date,
        note=tx.note,
        status=tx.status.value,
        created_at=tx.created_at.isoformat(),
        cancelled_at=tx.cancelled_at.isoformat() if tx.cancelled_at else None,
        account_id=tx.account_id,
        user_id=tx.user_id,
    )


@router.post("/transactions/{tx_id}/cancel", response_model=AccountingTransactionResponse)
def cancel_transaction(
    tx_id: int,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> AccountingTransactionResponse:
    """Cancel an active transaction."""
    service = _get_service(request)
    cancelled = service.cancel(tx_id=tx_id, user_id=session.user_id)
    if cancelled is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active transaction not found",
        )
    return AccountingTransactionResponse(
        id=cancelled.id,
        chat_id=cancelled.chat_id,
        tx_type=cancelled.tx_type.value,
        amount_cents=cancelled.amount_cents,
        tx_date=cancelled.tx_date,
        note=cancelled.note,
        status=cancelled.status.value,
        created_at=cancelled.created_at.isoformat(),
        cancelled_at=cancelled.cancelled_at.isoformat() if cancelled.cancelled_at else None,
        account_id=cancelled.account_id,
        user_id=cancelled.user_id,
    )

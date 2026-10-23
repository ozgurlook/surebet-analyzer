"""Source-independent evaluation of the direct-cash loss bonus."""

from datetime import UTC, datetime, timedelta
from typing import ClassVar

from pydantic import (
    ConfigDict,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from surebet.domain.models import BonusState, DomainModel

_TWENTY_TL_CENTS = 2_000
_ONE_HUNDRED_TL_CENTS = 10_000
_PERIOD = timedelta(hours=72)

REASON_CODES = (
    "balance_not_below_20tl",
    "loss_below_100tl",
    "deposit_below_100tl",
    "period_expired",
    "withdrawals_exceed_deposits",
    "unrelated_open_bets",
    "requirements_unconfirmed",
)


class BonusEvaluation(DomainModel):
    """Immutable result of evaluating one bonus state at one instant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount_cents: int
    eligible: StrictBool
    reasons: tuple[StrictStr, ...]

    _reason_codes: ClassVar[tuple[str, ...]] = REASON_CODES

    @field_validator("amount_cents", mode="before")
    @classmethod
    def _strict_nonnegative_amount(cls, value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("amount_cents must be a nonnegative integer")
        return value

    @field_validator("reasons", mode="before")
    @classmethod
    def _strict_reasons_tuple(cls, value: object) -> object:
        if not isinstance(value, tuple):
            raise ValueError("reasons must be a tuple")  # noqa: TRY004
        return value

    @model_validator(mode="after")
    def _validate_invariants(self) -> "BonusEvaluation":
        reasons = self.reasons
        if any(reason not in self._reason_codes for reason in reasons):
            raise ValueError("reasons contain an unknown reason code")
        if len(set(reasons)) != len(reasons):
            raise ValueError("reasons must not contain duplicates")
        expected_order = tuple(reason for reason in self._reason_codes if reason in reasons)
        if reasons != expected_order:
            raise ValueError("reasons must use canonical order")
        if self.eligible != (not reasons):
            raise ValueError("eligible must equal whether reasons is empty")
        if self.eligible and self.amount_cents <= 0:
            raise ValueError("eligible evaluations require a positive amount")
        if not self.eligible and self.amount_cents != 0:
            raise ValueError("ineligible evaluations require zero amount")
        return self


def _validate_money(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError(f"{name} must be a nonnegative integer")
    return value


def _validate_now(state: BonusState, now: object) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError("now must be a timezone-aware datetime")
    instant = now.astimezone(UTC)
    anchor = state.period_anchor.astimezone(UTC)
    if instant < anchor:
        raise ValueError("now must not be before period_anchor")
    return instant


def _validate_common(
    state: object,
    now: object,
    assume_confirmed: object,
    *,
    money: tuple[tuple[str, object], ...],
) -> tuple[BonusState, datetime]:
    if not isinstance(state, BonusState):
        raise TypeError("state must be a BonusState")
    for name, value in money:
        _validate_money(value, name)
    if type(assume_confirmed) is not bool:
        raise TypeError("assume_confirmed must be a bool")
    return state, _validate_now(state, now)


def claimable_bonus(
    state: BonusState,
    *,
    balance_cents: int,
    loss_cents: int,
    now: datetime,
    assume_confirmed: bool = False,
) -> BonusEvaluation:
    """Evaluate the claimable amount for a settled counterfactual outcome."""

    state, instant = _validate_common(
        state,
        now,
        assume_confirmed,
        money=(("balance_cents", balance_cents), ("loss_cents", loss_cents)),
    )
    reasons: list[str] = []
    if balance_cents >= _TWENTY_TL_CENTS:
        reasons.append("balance_not_below_20tl")
    if loss_cents < _ONE_HUNDRED_TL_CENTS:
        reasons.append("loss_below_100tl")
    if state.net_deposit_cents < _ONE_HUNDRED_TL_CENTS:
        reasons.append("deposit_below_100tl")
    elapsed = instant - state.period_anchor.astimezone(UTC)
    if elapsed >= _PERIOD:
        reasons.append("period_expired")
    if state.withdrawals_exceed_deposits:
        reasons.append("withdrawals_exceed_deposits")
    if state.unrelated_open_bets:
        reasons.append("unrelated_open_bets")
    if not state.requirements_confirmed and not assume_confirmed:
        reasons.append("requirements_unconfirmed")

    eligible = not reasons
    return BonusEvaluation(
        amount_cents=loss_cents // 10 if eligible else 0,
        eligible=eligible,
        reasons=tuple(reasons),
    )


def bonus_delta(
    state: BonusState,
    *,
    before_balance_cents: int,
    after_balance_cents: int,
    after_loss_cents: int,
    now: datetime,
    assume_confirmed: bool = False,
) -> int:
    """Return the after-minus-before change in claimable bonus entitlement."""

    _validate_common(
        state,
        now,
        assume_confirmed,
        money=(
            ("before_balance_cents", before_balance_cents),
            ("after_balance_cents", after_balance_cents),
            ("after_loss_cents", after_loss_cents),
        ),
    )
    before = claimable_bonus(
        state,
        balance_cents=before_balance_cents,
        loss_cents=state.loss_before_cents,
        now=now,
        assume_confirmed=assume_confirmed,
    )
    after = claimable_bonus(
        state,
        balance_cents=after_balance_cents,
        loss_cents=after_loss_cents,
        now=now,
        assume_confirmed=assume_confirmed,
    )
    return after.amount_cents - before.amount_cents

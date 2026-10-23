from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from surebet.bonus.engine import BonusEvaluation, bonus_delta, claimable_bonus
from surebet.domain.models import BonusState

NOW = datetime(2026, 8, 23, 18, tzinfo=UTC)


def state(**changes: object) -> BonusState:
    values: dict[str, object] = {
        "loss_before_cents": 10_000,
        "net_deposit_cents": 10_000,
        "period_anchor": NOW - timedelta(hours=1),
        "withdrawals_exceed_deposits": False,
        "unrelated_open_bets": False,
        "requirements_confirmed": True,
    }
    values.update(changes)
    return BonusState.model_validate(values)


def test_claimable_bonus_enforces_balance_strictly_below_twenty_tl() -> None:
    assert (
        claimable_bonus(state(), balance_cents=1_999, loss_cents=10_000, now=NOW).amount_cents
        == 1_000
    )
    assert (
        claimable_bonus(state(), balance_cents=2_000, loss_cents=10_000, now=NOW).amount_cents == 0
    )


def test_claimable_bonus_expires_at_seventy_two_hours() -> None:
    expired = state(period_anchor=NOW - timedelta(hours=72))
    assert claimable_bonus(expired, balance_cents=0, loss_cents=10_000, now=NOW).amount_cents == 0


def test_bonus_delta_can_be_negative() -> None:
    value = bonus_delta(
        state(), before_balance_cents=0, after_balance_cents=0, after_loss_cents=5_000, now=NOW
    )
    assert value == -1_000


@pytest.mark.parametrize(
    ("loss_cents", "amount_cents"), [(9_999, 0), (10_000, 1_000), (10_001, 1_000), (10_099, 1_009)]
)
def test_claimable_bonus_uses_loss_threshold_and_integer_ten_percent(
    loss_cents: int, amount_cents: int
) -> None:
    evaluation = claimable_bonus(state(), balance_cents=0, loss_cents=loss_cents, now=NOW)
    assert evaluation.amount_cents == amount_cents
    assert evaluation.eligible is (amount_cents > 0)


@pytest.mark.parametrize("deposit_cents", [9_999, 10_000])
def test_claimable_bonus_enforces_deposit_threshold(deposit_cents: int) -> None:
    evaluation = claimable_bonus(
        state(net_deposit_cents=deposit_cents), balance_cents=0, loss_cents=10_000, now=NOW
    )
    assert evaluation.eligible is (deposit_cents == 10_000)
    assert evaluation.reasons == (() if deposit_cents == 10_000 else ("deposit_below_100tl",))


def test_claimable_bonus_uses_elapsed_utc_time_at_exact_boundary() -> None:
    anchor = datetime(2026, 3, 7, 1, 30, tzinfo=ZoneInfo("America/New_York"))
    expired_now = datetime(2026, 3, 10, 2, 30, tzinfo=ZoneInfo("America/New_York"))
    before_expiry = expired_now - timedelta(minutes=1)
    assert (expired_now.astimezone(UTC) - anchor.astimezone(UTC)) == timedelta(hours=72)
    assert claimable_bonus(
        state(period_anchor=anchor), balance_cents=0, loss_cents=10_000, now=expired_now
    ).reasons == ("period_expired",)
    assert claimable_bonus(
        state(period_anchor=anchor), balance_cents=0, loss_cents=10_000, now=before_expiry
    ).eligible


def test_claimable_bonus_treats_equivalent_aware_offsets_as_same_instant() -> None:
    anchor = datetime(2026, 8, 20, 15, 1, tzinfo=UTC)
    now = datetime(2026, 8, 23, 18, tzinfo=ZoneInfo("Europe/Istanbul"))
    assert now.astimezone(UTC) == datetime(2026, 8, 23, 15, tzinfo=UTC)
    assert claimable_bonus(
        state(period_anchor=anchor), balance_cents=0, loss_cents=10_000, now=now
    ).eligible


def test_claimable_bonus_reports_all_reasons_in_canonical_order() -> None:
    evaluation = claimable_bonus(
        state(
            net_deposit_cents=9_999,
            period_anchor=NOW - timedelta(hours=72),
            withdrawals_exceed_deposits=True,
            unrelated_open_bets=True,
            requirements_confirmed=False,
        ),
        balance_cents=2_000,
        loss_cents=9_999,
        now=NOW,
    )
    assert evaluation == BonusEvaluation(
        amount_cents=0,
        eligible=False,
        reasons=(
            "balance_not_below_20tl",
            "loss_below_100tl",
            "deposit_below_100tl",
            "period_expired",
            "withdrawals_exceed_deposits",
            "unrelated_open_bets",
            "requirements_unconfirmed",
        ),
    )


def test_claimable_bonus_flags_withdrawals_and_open_bets() -> None:
    for field in ("withdrawals_exceed_deposits", "unrelated_open_bets"):
        evaluation = claimable_bonus(
            state(**{field: True}), balance_cents=0, loss_cents=10_000, now=NOW
        )
        assert evaluation.reasons == (field,)


def test_assume_confirmed_suppresses_only_unconfirmed_reason() -> None:
    unconfirmed = state(requirements_confirmed=False)
    assert claimable_bonus(unconfirmed, balance_cents=0, loss_cents=10_000, now=NOW).reasons == (
        "requirements_unconfirmed",
    )
    assert (
        claimable_bonus(
            unconfirmed, balance_cents=0, loss_cents=10_000, now=NOW, assume_confirmed=True
        ).amount_cents
        == 1_000
    )
    assert claimable_bonus(
        state(requirements_confirmed=False),
        balance_cents=2_000,
        loss_cents=10_000,
        now=NOW,
        assume_confirmed=True,
    ).reasons == ("balance_not_below_20tl",)


@pytest.mark.parametrize(
    ("state_changes", "reason"),
    [
        ({"net_deposit_cents": 9_999}, "deposit_below_100tl"),
        ({"period_anchor": NOW - timedelta(hours=72)}, "period_expired"),
        ({"withdrawals_exceed_deposits": True}, "withdrawals_exceed_deposits"),
        ({"unrelated_open_bets": True}, "unrelated_open_bets"),
    ],
)
def test_assume_confirmed_does_not_bypass_structural_rules(
    state_changes: dict[str, object], reason: str
) -> None:
    unconfirmed = state(loss_before_cents=5_000, requirements_confirmed=False, **state_changes)

    normal = claimable_bonus(
        unconfirmed, balance_cents=0, loss_cents=10_000, now=NOW, assume_confirmed=False
    )
    confirmed = claimable_bonus(
        unconfirmed, balance_cents=0, loss_cents=10_000, now=NOW, assume_confirmed=True
    )
    assert reason in normal.reasons
    assert "requirements_unconfirmed" in normal.reasons
    assert confirmed.reasons == (reason,)
    assert confirmed.amount_cents == 0

    assert (
        bonus_delta(
            unconfirmed,
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=10_000,
            now=NOW,
            assume_confirmed=False,
        )
        == 0
    )
    assert (
        bonus_delta(
            unconfirmed,
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=10_000,
            now=NOW,
            assume_confirmed=True,
        )
        == 0
    )


def test_bonus_delta_is_incremental_for_preserved_entitlement() -> None:
    assert (
        bonus_delta(
            state(), before_balance_cents=0, after_balance_cents=0, after_loss_cents=12_000, now=NOW
        )
        == 200
    )
    assert (
        bonus_delta(
            state(), before_balance_cents=0, after_balance_cents=0, after_loss_cents=10_000, now=NOW
        )
        == 0
    )
    assert (
        bonus_delta(
            state(),
            before_balance_cents=0,
            after_balance_cents=2_000,
            after_loss_cents=10_000,
            now=NOW,
        )
        == -1_000
    )


@pytest.mark.parametrize("bad", [-1, True, False, 1.0, "1000"])
def test_claimable_bonus_rejects_invalid_money(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        claimable_bonus(state(), balance_cents=cast(Any, bad), loss_cents=10_000, now=NOW)
    with pytest.raises((TypeError, ValueError)):
        claimable_bonus(state(), balance_cents=0, loss_cents=cast(Any, bad), now=NOW)


@pytest.mark.parametrize("bad", [-1, True, 1.0, "1000"])
def test_bonus_delta_rejects_invalid_money(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        bonus_delta(
            state(),
            before_balance_cents=cast(Any, bad),
            after_balance_cents=0,
            after_loss_cents=0,
            now=NOW,
        )
    with pytest.raises((TypeError, ValueError)):
        bonus_delta(
            state(),
            before_balance_cents=0,
            after_balance_cents=cast(Any, bad),
            after_loss_cents=0,
            now=NOW,
        )
    with pytest.raises((TypeError, ValueError)):
        bonus_delta(
            state(),
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=cast(Any, bad),
            now=NOW,
        )


@pytest.mark.parametrize("bad", [NOW.replace(tzinfo=None), "2026-08-23T18:00:00Z", 1, None])
def test_public_functions_reject_invalid_now(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        claimable_bonus(state(), balance_cents=0, loss_cents=10_000, now=cast(Any, bad))
    with pytest.raises((TypeError, ValueError)):
        bonus_delta(
            state(),
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=10_000,
            now=cast(Any, bad),
        )


@pytest.mark.parametrize("bad", [0, 1, "yes", SimpleNamespace()])
def test_public_functions_require_actual_bool_assume_confirmed(bad: object) -> None:
    with pytest.raises(TypeError):
        claimable_bonus(
            state(), balance_cents=0, loss_cents=10_000, now=NOW, assume_confirmed=cast(Any, bad)
        )
    with pytest.raises(TypeError):
        bonus_delta(
            state(),
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=10_000,
            now=NOW,
            assume_confirmed=cast(Any, bad),
        )


def test_public_functions_reject_wrong_state_and_future_anchor() -> None:
    with pytest.raises(TypeError):
        claimable_bonus(cast(Any, object()), balance_cents=0, loss_cents=10_000, now=NOW)
    with pytest.raises(TypeError):
        bonus_delta(
            cast(Any, object()),
            before_balance_cents=0,
            after_balance_cents=0,
            after_loss_cents=10_000,
            now=NOW,
        )
    future = state(period_anchor=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        claimable_bonus(future, balance_cents=0, loss_cents=10_000, now=NOW)
    with pytest.raises(ValueError):
        bonus_delta(
            future, before_balance_cents=0, after_balance_cents=0, after_loss_cents=10_000, now=NOW
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount_cents": True, "eligible": True, "reasons": ()},
        {"amount_cents": 1, "eligible": 1, "reasons": ()},
        {"amount_cents": 1, "eligible": True, "reasons": [()]},
        {"amount_cents": 1, "eligible": True, "reasons": ("unknown",)},
        {"amount_cents": 1, "eligible": True, "reasons": ("loss_below_100tl", "loss_below_100tl")},
        {
            "amount_cents": 1,
            "eligible": True,
            "reasons": ("period_expired", "balance_not_below_20tl"),
        },
        {"amount_cents": 1, "eligible": False, "reasons": ()},
        {"amount_cents": 0, "eligible": True, "reasons": ()},
        {"amount_cents": 1, "eligible": False, "reasons": ("loss_below_100tl",)},
        {"amount_cents": 1, "eligible": True, "reasons": (), "extra": "forbidden"},
    ],
)
def test_bonus_evaluation_rejects_invalid_direct_construction(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BonusEvaluation.model_validate(kwargs)


def test_evaluation_is_frozen_and_repeated_calls_are_deterministic() -> None:
    before = state()
    first = claimable_bonus(before, balance_cents=0, loss_cents=10_001, now=NOW)
    second = claimable_bonus(before, balance_cents=0, loss_cents=10_001, now=NOW)
    assert first == second
    assert before == state()
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        first.amount_cents = 2_000

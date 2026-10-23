from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from ortools.sat.python import cp_model

import surebet.arbitrage.solver as solver_module
from surebet.arbitrage.scenarios import evaluate_scenarios
from surebet.arbitrage.solver import optimize_adjusted
from surebet.domain.enums import Outcome, Source
from surebet.domain.models import (
    Allocation,
    AnalysisRequest,
    BonusState,
    Quote,
    SourceAccount,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _quotes() -> dict[Outcome, Quote]:
    return {
        outcome: Quote(
            source=source,
            event_id="event-1",
            outcome=outcome,
            odds=odds,
            observed_at=NOW,
        )
        for outcome, source, odds in (
            (Outcome.HOME, Source.SOURCE_A, "1.50"),
            (Outcome.DRAW, Source.SOURCE_B, "2.00"),
            (Outcome.AWAY, Source.SOURCE_C, "2.00"),
        )
    }


def _accounts(*, anchor: datetime = NOW) -> dict[Source, SourceAccount]:
    return {
        Source.SOURCE_A: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
        Source.SOURCE_B: SourceAccount(
            balance_cents=200,
            max_stake_cents=100_000,
            bonus=BonusState(
                loss_before_cents=9_950,
                net_deposit_cents=10_000,
                period_anchor=anchor,
            ),
        ),
        Source.SOURCE_C: SourceAccount(
            balance_cents=200,
            max_stake_cents=100_000,
            bonus=BonusState(
                loss_before_cents=9_950,
                net_deposit_cents=10_000,
                period_anchor=anchor,
            ),
        ),
    }


def _request(*, evaluation_at: datetime | None = None, anchor: datetime = NOW) -> AnalysisRequest:
    return AnalysisRequest(
        budget_cents=300,
        now=NOW,
        bonus_evaluation_at=evaluation_at,
        accounts=_accounts(anchor=anchor),
    )


def _request_for(
    accounts: dict[Source, SourceAccount],
    *,
    evaluation_at: datetime | None = NOW + timedelta(hours=1),
) -> AnalysisRequest:
    return AnalysisRequest(
        budget_cents=300,
        now=NOW,
        bonus_evaluation_at=evaluation_at,
        accounts=accounts,
    )


def test_missing_bonus_time_is_fail_closed() -> None:
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(_quotes(), allocation, _request())

    assert all(item.bonus_delta_cents == 0 for item in scenarios)
    assert optimize_adjusted(_quotes(), _request()) is None


def test_bonus_settlement_boundary_and_offset_equivalence() -> None:
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    before_expiry = _request(evaluation_at=NOW + timedelta(hours=71, minutes=59))
    at_expiry = _request(evaluation_at=NOW + timedelta(hours=72))
    offset = datetime(2026, 8, 24, 18, tzinfo=ZoneInfo("Europe/Istanbul"))
    assert offset.astimezone(UTC) == NOW + timedelta(hours=3)

    assert any(
        item.bonus_delta_cents for item in evaluate_scenarios(_quotes(), allocation, before_expiry)
    )
    assert all(
        item.bonus_delta_cents == 0 for item in evaluate_scenarios(_quotes(), allocation, at_expiry)
    )
    assert evaluate_scenarios(
        _quotes(), allocation, _request(evaluation_at=offset)
    ) == evaluate_scenarios(_quotes(), allocation, _request(evaluation_at=NOW + timedelta(hours=3)))


def test_post_settlement_before_expiry_can_make_allocation_adjusted_profitable() -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(_quotes(), allocation, request)

    assert scenarios[0].adjusted_profit_cents > 0


def test_expired_bonus_contributes_no_delta() -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=72))
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(_quotes(), allocation, request)

    assert all(item.bonus_delta_cents == 0 for item in scenarios)


def test_source_b_and_source_c_bonus_deltas_are_accounted_independently() -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(_quotes(), allocation, request)

    assert tuple(item.bonus_delta_cents for item in scenarios) == (2_010, 1_005, 1_005)


def test_optimizer_returns_real_strict_and_assumed_profitable_allocations() -> None:
    quotes = _quotes()
    strict_request = _request(evaluation_at=NOW + timedelta(hours=1))
    strict = optimize_adjusted(quotes, strict_request)
    assert strict is not None
    assert (
        min(
            item.adjusted_profit_cents
            for item in evaluate_scenarios(quotes, strict, strict_request)
        )
        > 0
    )

    accounts = _accounts()
    for source in (Source.SOURCE_B, Source.SOURCE_C):
        state = accounts[source].bonus
        assert state is not None
        accounts[source] = accounts[source].model_copy(
            update={"bonus": state.model_copy(update={"requirements_confirmed": False})}
        )
    assumed_request = _request_for(accounts)
    assert optimize_adjusted(quotes, assumed_request) is None
    assumed = optimize_adjusted(quotes, assumed_request, assume_confirmed=True)
    assert assumed is not None
    assert (
        min(
            item.adjusted_profit_cents
            for item in evaluate_scenarios(quotes, assumed, assumed_request, assume_confirmed=True)
        )
        > 0
    )


@pytest.mark.parametrize("bad_mode", [0, 1, "yes", None])
def test_public_adjusted_apis_reject_non_bool_confirmation(bad_mode: object) -> None:
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    with pytest.raises(TypeError):
        evaluate_scenarios(_quotes(), allocation, request, assume_confirmed=bad_mode)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        optimize_adjusted(_quotes(), request, assume_confirmed=bad_mode)  # type: ignore[arg-type]


def test_bonus_on_unused_account_does_not_participate() -> None:
    quotes = _quotes()
    quotes[Outcome.DRAW] = quotes[Outcome.DRAW].model_copy(update={"source": Source.SOURCE_C})
    accounts = _accounts()
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(quotes, allocation, request)

    assert all(item.bonus_delta_cents == 0 for item in scenarios)
    assert optimize_adjusted(quotes, request) is None


def test_assigned_bonus_before_period_anchor_fails_before_cp_sat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=1), anchor=NOW + timedelta(hours=2))
    monkeypatch.setattr(solver_module.cp_model, "CpModel", lambda: pytest.fail("model constructed"))

    with pytest.raises(ValueError, match="period_anchor"):
        optimize_adjusted(_quotes(), request)


def test_same_source_multiple_outcomes_uses_all_site_stakes() -> None:
    quotes = _quotes()
    quotes[Outcome.HOME] = quotes[Outcome.HOME].model_copy(update={"source": Source.SOURCE_B})
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})

    scenarios = evaluate_scenarios(quotes, allocation, request)

    # Source B has 200 cents at risk, including the non-winning selection.
    assert scenarios[0].bonus_delta_cents == 2_005


def test_existing_claimable_bonus_is_subtracted_and_can_be_destroyed() -> None:
    accounts = _accounts()
    state = accounts[Source.SOURCE_B].bonus
    assert state is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={
            "balance_cents": 1_000,
            "max_stake_cents": 1_000,
            "bonus": state.model_copy(update={"loss_before_cents": 10_000}),
        }
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    quotes = _quotes()

    scenarios = evaluate_scenarios(quotes, allocation, request)

    assert scenarios[1].bonus_delta_cents == -1_000


@pytest.mark.parametrize("balance, expected", [(2_099, 1_005), (2_100, 0)])
def test_balance_threshold_is_strictly_below_twenty_tl(balance: int, expected: int) -> None:
    accounts = _accounts()
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"balance_cents": balance, "max_stake_cents": balance}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    quotes = _quotes()
    scenarios = evaluate_scenarios(quotes, allocation, request)
    assert scenarios[0].bonus_delta_cents == expected


@pytest.mark.parametrize("loss_before, expected", [(9_900, 1_000), (9_899, 0)])
def test_loss_threshold_is_at_least_one_hundred_tl(loss_before: int, expected: int) -> None:
    accounts = _accounts()
    state = accounts[Source.SOURCE_B].bonus
    assert state is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"bonus": state.model_copy(update={"loss_before_cents": loss_before})}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    scenarios = evaluate_scenarios(_quotes(), allocation, request)
    assert scenarios[0].bonus_delta_cents == expected


@pytest.mark.parametrize(
    ("balance", "loss_before", "expected_delta"),
    [(2_099, 9_950, 1_005), (2_100, 9_950, 0), (2_099, 9_900, 1_000), (2_099, 9_899, 0)],
)
def test_adjusted_optimizer_matches_dynamic_threshold_boundaries(
    balance: int, loss_before: int, expected_delta: int
) -> None:
    accounts = _accounts()
    source_b = accounts[Source.SOURCE_B].bonus
    assert source_b is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={
            "balance_cents": balance,
            "min_stake_cents": 100,
            "max_stake_cents": 100,
            "bonus": source_b.model_copy(update={"loss_before_cents": loss_before}),
        }
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    accounts[Source.SOURCE_A] = accounts[Source.SOURCE_A].model_copy(
        update={"min_stake_cents": 100, "max_stake_cents": 100}
    )
    request = _request_for(accounts)
    request = request.model_copy(update={"budget_cents": 300})
    quotes = {
        outcome: quote.model_copy(update={"odds": Decimal("4.00")})
        for outcome, quote in _quotes().items()
    }

    allocation = optimize_adjusted(quotes, request)

    assert allocation is not None
    scenario = evaluate_scenarios(quotes, allocation, request)[0]
    assert scenario.bonus_delta_cents == expected_delta


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("net_deposit_cents", 9_999),
        ("withdrawals_exceed_deposits", True),
        ("unrelated_open_bets", True),
    ],
)
def test_structural_bonus_blockers_remain_in_both_confirmation_modes(
    field: str, value: object
) -> None:
    accounts = _accounts()
    state = accounts[Source.SOURCE_B].bonus
    assert state is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"bonus": state.model_copy(update={field: value})}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    quotes = _quotes()

    normal = evaluate_scenarios(quotes, allocation, request)
    assumed = evaluate_scenarios(quotes, allocation, request, assume_confirmed=True)

    assert normal[0].bonus_delta_cents == assumed[0].bonus_delta_cents == 0


@pytest.mark.parametrize(
    "field", ["net_deposit_cents", "withdrawals_exceed_deposits", "unrelated_open_bets"]
)
def test_adjusted_optimizer_preserves_each_structural_blocker(field: str) -> None:
    accounts = _accounts()
    source_b = accounts[Source.SOURCE_B].bonus
    source_c = accounts[Source.SOURCE_C].bonus
    assert source_b is not None and source_c is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={
            "min_stake_cents": 100,
            "max_stake_cents": 100,
            "bonus": source_b.model_copy(
                update={field: 9_999 if field == "net_deposit_cents" else True}
            ),
        }
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(
        update={
            "min_stake_cents": 100,
            "max_stake_cents": 100,
            "bonus": source_c.model_copy(update={"loss_before_cents": 10_001}),
        }
    )
    accounts[Source.SOURCE_A] = accounts[Source.SOURCE_A].model_copy(
        update={"min_stake_cents": 100, "max_stake_cents": 100}
    )
    request = _request_for(accounts).model_copy(update={"budget_cents": 300})
    quotes = {
        outcome: quote.model_copy(update={"odds": Decimal("20.00")})
        for outcome, quote in _quotes().items()
    }

    allocation = optimize_adjusted(quotes, request)

    assert allocation is not None
    scenarios = evaluate_scenarios(quotes, allocation, request)
    assert scenarios[0].bonus_delta_cents == 10
    assert evaluate_scenarios(quotes, allocation, request, assume_confirmed=True) == scenarios


def test_unconfirmed_bonus_is_only_bypassed_in_assumed_mode() -> None:
    accounts = _accounts()
    state = accounts[Source.SOURCE_B].bonus
    assert state is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"bonus": state.model_copy(update={"requirements_confirmed": False})}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    quotes = _quotes()

    assert evaluate_scenarios(quotes, allocation, request)[0].bonus_delta_cents == 0
    assert (
        evaluate_scenarios(quotes, allocation, request, assume_confirmed=True)[0].bonus_delta_cents
        > 0
    )


def test_odd_cent_payout_and_bonus_floor_are_conservative() -> None:
    quotes = _quotes()
    quotes[Outcome.HOME] = quotes[Outcome.HOME].model_copy(update={"odds": Decimal("3.3333")})
    accounts = _accounts()
    source_b = accounts[Source.SOURCE_B].bonus
    assert source_b is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"bonus": source_b.model_copy(update={"loss_before_cents": 10_001})}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    allocation = Allocation(stakes_cents={Outcome.HOME: 100, Outcome.DRAW: 100, Outcome.AWAY: 100})

    scenario = evaluate_scenarios(quotes, allocation, request)[0]

    assert scenario.gross_return_cents == 333
    assert scenario.bonus_delta_cents == 10
    assert 10_001 + 100 == 10_101
    assert 10_101 // 10 == 1_010


def test_large_structural_deposit_is_not_sent_to_cp_sat() -> None:
    accounts = _accounts()
    state = accounts[Source.SOURCE_B].bonus
    assert state is not None
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"bonus": state.model_copy(update={"net_deposit_cents": 10**100})}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)

    assert optimize_adjusted(_quotes(), request) is None


def test_adjusted_solver_respects_budget_min_max_and_shared_source_balance() -> None:
    quotes = _quotes()
    quotes[Outcome.HOME] = quotes[Outcome.HOME].model_copy(update={"odds": Decimal("5.00")})
    quotes[Outcome.DRAW] = quotes[Outcome.DRAW].model_copy(
        update={"source": Source.SOURCE_B, "odds": Decimal("5.00")}
    )
    quotes[Outcome.AWAY] = quotes[Outcome.AWAY].model_copy(
        update={"source": Source.SOURCE_B, "odds": Decimal("5.00")}
    )
    accounts = _accounts()
    accounts[Source.SOURCE_A] = accounts[Source.SOURCE_A].model_copy(
        update={"min_stake_cents": 200, "max_stake_cents": 201}
    )
    accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
        update={"balance_cents": 700, "min_stake_cents": 300, "max_stake_cents": 400}
    )
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(update={"bonus": None})
    request = _request_for(accounts)
    request = request.model_copy(update={"budget_cents": 900})

    allocation = optimize_adjusted(quotes, request)

    assert allocation is not None
    assert 200 <= allocation.stakes_cents[Outcome.HOME] <= 201
    assert 300 <= allocation.stakes_cents[Outcome.DRAW] <= 400
    assert 300 <= allocation.stakes_cents[Outcome.AWAY] <= 400
    assert allocation.stakes_cents[Outcome.DRAW] + allocation.stakes_cents[Outcome.AWAY] <= 700
    assert sum(allocation.stakes_cents.values()) <= 900


def test_adjusted_solver_respects_floored_max_payout_cap() -> None:
    quotes = {
        outcome: Quote(
            source=source,
            event_id="event-1",
            outcome=outcome,
            odds="3.3333",
            observed_at=NOW,
        )
        for outcome, source in zip(
            Outcome, (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C), strict=True
        )
    }
    accounts = {
        source: SourceAccount(
            balance_cents=1_001,
            min_stake_cents=1_001,
            max_stake_cents=1_001,
            max_payout_cents=3_335 if source is Source.SOURCE_A else None,
            bonus=(
                BonusState(
                    loss_before_cents=10_000,
                    net_deposit_cents=10_000,
                    period_anchor=NOW - timedelta(hours=72),
                )
                if source is Source.SOURCE_B
                else None
            ),
        )
        for source in (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C)
    }
    request = _request_for(accounts)
    request = request.model_copy(update={"budget_cents": 3_003})

    assert optimize_adjusted(quotes, request) is None
    accounts[Source.SOURCE_A] = accounts[Source.SOURCE_A].model_copy(
        update={"max_payout_cents": 3_336}
    )
    assert (
        optimize_adjusted(quotes, _request_for(accounts).model_copy(update={"budget_cents": 3_003}))
        is not None
    )


def test_adjusted_solver_returns_none_without_bonus_time_source_or_profit() -> None:
    quotes = _quotes()
    no_bonus_accounts = {
        source: account.model_copy(update={"bonus": None})
        for source, account in _accounts().items()
    }
    no_bonus = _request_for(no_bonus_accounts)
    assert optimize_adjusted(quotes, no_bonus) is None

    infeasible_accounts = _accounts()
    for source in (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C):
        infeasible_accounts[source] = infeasible_accounts[source].model_copy(
            update={"min_stake_cents": 400, "max_stake_cents": 1_000}
        )
    assert (
        optimize_adjusted(
            quotes, _request_for(infeasible_accounts).model_copy(update={"budget_cents": 1_199})
        )
        is None
    )

    expired_accounts = _accounts(anchor=NOW - timedelta(hours=72))
    low_quotes = {
        outcome: quote.model_copy(update={"odds": Decimal("2.00")})
        for outcome, quote in quotes.items()
    }
    assert optimize_adjusted(low_quotes, _request_for(expired_accounts)) is None


def test_adjusted_solver_is_deterministic_across_mapping_order_and_repeats() -> None:
    quotes = _quotes()
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    results = [optimize_adjusted(dict(reversed(tuple(quotes.items()))), request) for _ in range(3)]

    assert results[0] is not None
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("status", [cp_model.FEASIBLE, cp_model.UNKNOWN])
def test_adjusted_solver_never_accepts_nonoptimal_statuses(
    monkeypatch: pytest.MonkeyPatch, status: cp_model.CpSolverStatus
) -> None:
    class FakeSolver:
        parameters = SimpleNamespace(num_search_workers=1, random_seed=0)

        def solve(self, model: cp_model.CpModel) -> cp_model.CpSolverStatus:
            return status

    monkeypatch.setattr(solver_module, "_new_solver", lambda: FakeSolver())

    assert optimize_adjusted(_quotes(), _request(evaluation_at=NOW + timedelta(hours=1))) is None


def test_adjusted_solver_reports_model_invalid_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solver_module, "_validate_model", lambda model: None)
    monkeypatch.setattr(cp_model.CpModel, "validate", lambda model: "adjusted-diagnostic")

    class InvalidSolver:
        parameters = SimpleNamespace(num_search_workers=1, random_seed=0)

        def solve(self, model: cp_model.CpModel) -> cp_model.CpSolverStatus:
            return cp_model.MODEL_INVALID

    monkeypatch.setattr(solver_module, "_new_solver", lambda: InvalidSolver())

    with pytest.raises(ValueError, match="adjusted-diagnostic"):
        optimize_adjusted(_quotes(), _request(evaluation_at=NOW + timedelta(hours=1)))


@pytest.mark.parametrize("field", ["balance_cents", "loss_before_cents"])
def test_adjusted_solver_rejects_oversized_cp_sat_inputs(field: str) -> None:
    accounts = _accounts()
    if field == "balance_cents":
        accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(update={field: 10**100})
    else:
        state = accounts[Source.SOURCE_B].bonus
        assert state is not None
        accounts[Source.SOURCE_B] = accounts[Source.SOURCE_B].model_copy(
            update={"bonus": state.model_copy(update={field: 10**100})}
        )

    with pytest.raises(ValueError, match="unsupported CP-SAT input"):
        optimize_adjusted(_quotes(), _request_for(accounts))


def test_adjusted_solver_rejects_aggregate_scenario_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = solver_module.evaluate_scenarios

    def disagree(quotes, allocation, request, *, assume_confirmed=False):
        scenarios = list(original(quotes, allocation, request, assume_confirmed=assume_confirmed))
        scenarios[0] = scenarios[0].model_copy(
            update={
                "bonus_delta_cents": scenarios[0].bonus_delta_cents + 1,
                "adjusted_profit_cents": scenarios[0].adjusted_profit_cents + 1,
            }
        )
        return tuple(scenarios)

    monkeypatch.setattr(solver_module, "evaluate_scenarios", disagree)

    assert optimize_adjusted(_quotes(), _request(evaluation_at=NOW + timedelta(hours=1))) is None


def test_adjusted_solver_rejects_equal_and_opposite_per_source_model_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = solver_module.claimable_bonus

    def disagree(state, *, balance_cents, loss_cents, now, assume_confirmed=False):
        result = original(
            state,
            balance_cents=balance_cents,
            loss_cents=loss_cents,
            now=now,
            assume_confirmed=assume_confirmed,
        )
        adjustment = 1 if state.loss_before_cents == 9_950 else -1
        return result.model_copy(update={"amount_cents": result.amount_cents + adjustment})

    monkeypatch.setattr(solver_module, "claimable_bonus", disagree)

    accounts = _accounts()
    source_b = accounts[Source.SOURCE_B].bonus
    source_c = accounts[Source.SOURCE_C].bonus
    assert source_b is not None and source_c is not None
    accounts[Source.SOURCE_C] = accounts[Source.SOURCE_C].model_copy(
        update={"bonus": source_c.model_copy(update={"loss_before_cents": 9_951})}
    )

    assert optimize_adjusted(_quotes(), _request_for(accounts)) is None


def test_adjusted_solver_matches_bounded_exact_bruteforce_oracle() -> None:
    quotes = {
        Outcome.HOME: Quote(
            source=Source.SOURCE_B,
            event_id="oracle-event",
            outcome=Outcome.HOME,
            odds="2.15",
            observed_at=NOW,
        ),
        Outcome.DRAW: Quote(
            source=Source.SOURCE_B,
            event_id="oracle-event",
            outcome=Outcome.DRAW,
            odds="7.23",
            observed_at=NOW,
        ),
        Outcome.AWAY: Quote(
            source=Source.SOURCE_C,
            event_id="oracle-event",
            outcome=Outcome.AWAY,
            odds="2.15",
            observed_at=NOW,
        ),
    }
    source_b_state = BonusState(
        loss_before_cents=9_995, net_deposit_cents=10_000, period_anchor=NOW
    )
    source_c_state = BonusState(
        loss_before_cents=9_997, net_deposit_cents=10_000, period_anchor=NOW
    )
    accounts = {
        Source.SOURCE_A: SourceAccount(balance_cents=8, min_stake_cents=1, max_stake_cents=8),
        Source.SOURCE_B: SourceAccount(
            balance_cents=8, min_stake_cents=1, max_stake_cents=8, bonus=source_b_state
        ),
        Source.SOURCE_C: SourceAccount(
            balance_cents=8, min_stake_cents=1, max_stake_cents=8, bonus=source_c_state
        ),
    }
    request = AnalysisRequest(
        budget_cents=10,
        now=NOW,
        bonus_evaluation_at=NOW + timedelta(hours=1),
        accounts=accounts,
    )
    feasible: list[tuple[int, int, tuple[int, int, int]]] = []
    for values in product(range(1, 9), repeat=3):
        if values[0] + values[1] > 8 or sum(values) > request.budget_cents:
            continue
        allocation = Allocation(stakes_cents=dict(zip(Outcome, values, strict=True)))
        scenarios = evaluate_scenarios(quotes, allocation, request)
        feasible.append(
            (min(item.adjusted_profit_cents for item in scenarios), sum(values), values)
        )
    expected = max(
        feasible, key=lambda item: (item[0], -item[1], tuple(-value for value in item[2]))
    )
    primary = [item for item in feasible if item[0] == expected[0]]
    minimum_total = min(item[1] for item in primary)
    minimum_total_ties = [item for item in primary if item[1] == minimum_total]
    assert len(primary) > len(minimum_total_ties)
    assert {item[1] for item in primary} == {8, 9, 10}
    assert len(minimum_total_ties) >= 2
    assert {item[2] for item in primary} == {
        (3, 2, 3),
        (4, 1, 3),
        (4, 1, 4),
        (4, 2, 4),
    }

    allocation = optimize_adjusted(quotes, request)

    assert allocation is not None
    values = tuple(allocation.stakes_cents[outcome] for outcome in Outcome)
    scenarios = evaluate_scenarios(quotes, allocation, request)
    assert (min(item.adjusted_profit_cents for item in scenarios), sum(values), values) == expected
    assert values == (3, 2, 3)  # minimum total, then HOME/DRAW/AWAY lexicographic stage

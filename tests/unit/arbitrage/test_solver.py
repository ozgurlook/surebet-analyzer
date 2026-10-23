from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from types import SimpleNamespace

import pytest
from ortools.sat.python import cp_model

import surebet.arbitrage.solver as solver_module
from surebet.arbitrage.solver import optimize_standard
from surebet.arbitrage.verify import verify_allocation
from surebet.domain.enums import Outcome, Source
from surebet.domain.models import AnalysisRequest, Quote, ScenarioResult, SourceAccount
from surebet.money import payout_cents

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _request(accounts: dict[Source, SourceAccount], budget: int = 100_000) -> AnalysisRequest:
    return AnalysisRequest(budget_cents=budget, now=NOW, accounts=accounts)


def _quotes(sources: dict[Outcome, Source] | None = None) -> dict[Outcome, Quote]:
    assigned = sources or {
        Outcome.HOME: Source.SOURCE_A,
        Outcome.DRAW: Source.SOURCE_B,
        Outcome.AWAY: Source.SOURCE_C,
    }
    odds = {Outcome.HOME: "2.90", Outcome.DRAW: "3.60", Outcome.AWAY: "3.10"}
    return {
        outcome: Quote(
            source=assigned[outcome],
            event_id="event-1",
            outcome=outcome,
            odds=odds[outcome],
            observed_at=NOW,
        )
        for outcome in Outcome
    }


def test_solver_returns_profitable_allocation_within_each_source_balance() -> None:
    accounts = {
        source: SourceAccount(balance_cents=40_000, min_stake_cents=100, max_stake_cents=40_000)
        for source in Source
    }

    allocation = optimize_standard(_quotes(), _request(accounts))

    assert allocation is not None
    assert all(stake <= 40_000 for stake in allocation.stakes_cents.values())
    assert min(item.standard_profit_cents for item in verify_allocation(_quotes(), allocation)) > 0


def test_solver_aggregates_stakes_for_a_shared_source_balance() -> None:
    quotes = _quotes(
        {
            Outcome.HOME: Source.SOURCE_B,
            Outcome.DRAW: Source.SOURCE_B,
            Outcome.AWAY: Source.SOURCE_C,
        }
    )
    accounts = {
        Source.SOURCE_A: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
        Source.SOURCE_B: SourceAccount(balance_cents=25_000, max_stake_cents=100_000),
        Source.SOURCE_C: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
    }

    allocation = optimize_standard(quotes, _request(accounts))

    assert allocation is not None
    assert allocation.stakes_cents[Outcome.HOME] + allocation.stakes_cents[Outcome.DRAW] <= 25_000


def test_solver_rejects_false_positive_from_conservative_payout_rounding() -> None:
    quotes = {
        Outcome.HOME: Quote(
            source=Source.SOURCE_A,
            event_id="event-1",
            outcome=Outcome.HOME,
            odds="2.01",
            observed_at=NOW,
        ),
        Outcome.DRAW: Quote(
            source=Source.SOURCE_B,
            event_id="event-1",
            outcome=Outcome.DRAW,
            odds="3.01",
            observed_at=NOW,
        ),
        Outcome.AWAY: Quote(
            source=Source.SOURCE_C,
            event_id="event-1",
            outcome=Outcome.AWAY,
            odds="3.01",
            observed_at=NOW,
        ),
    }
    accounts = {
        source: SourceAccount(balance_cents=100, min_stake_cents=100, max_stake_cents=100)
        for source in Source
    }

    assert optimize_standard(quotes, _request(accounts, budget=300)) is None


def test_solver_respects_per_selection_minimum_and_maximum() -> None:
    accounts = {
        Source.SOURCE_A: SourceAccount(
            balance_cents=10_000, min_stake_cents=200, max_stake_cents=201
        ),
        Source.SOURCE_B: SourceAccount(
            balance_cents=10_000, min_stake_cents=300, max_stake_cents=301
        ),
        Source.SOURCE_C: SourceAccount(
            balance_cents=10_000, min_stake_cents=400, max_stake_cents=401
        ),
    }
    quotes = {
        outcome: quote.model_copy(update={"odds": Decimal("5.00")})
        for outcome, quote in _quotes().items()
    }

    allocation = optimize_standard(quotes, _request(accounts, budget=903))

    assert allocation is not None
    assert 200 <= allocation.stakes_cents[Outcome.HOME] <= 201
    assert 300 <= allocation.stakes_cents[Outcome.DRAW] <= 301
    assert 400 <= allocation.stakes_cents[Outcome.AWAY] <= 401


def test_solver_returns_none_when_budget_cannot_cover_selection_minima() -> None:
    accounts = {
        source: SourceAccount(balance_cents=10_000, min_stake_cents=400, max_stake_cents=10_000)
        for source in Source
    }

    assert optimize_standard(_quotes(), _request(accounts, budget=1_199)) is None


def test_solver_allows_floored_gross_equal_to_payout_cap() -> None:
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
            max_payout_cents=3_336 if source is Source.SOURCE_A else None,
        )
        for source in (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C)
    }

    allocation = optimize_standard(quotes, _request(accounts, budget=3_003))

    assert allocation is not None
    assert allocation.stakes_cents[Outcome.HOME] == 1_001
    assert verify_allocation(quotes, allocation)[0].gross_return_cents == 3_336


def test_solver_rejects_floored_gross_above_payout_cap() -> None:
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
        )
        for source in (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C)
    }

    assert optimize_standard(quotes, _request(accounts, budget=3_003)) is None


def test_solver_returns_none_for_nonprofitable_odds() -> None:
    quotes = {
        outcome: quote.model_copy(update={"odds": Decimal("2.00")})
        for outcome, quote in _quotes().items()
    }
    accounts = {
        source: SourceAccount(balance_cents=10_000, min_stake_cents=100, max_stake_cents=10_000)
        for source in Source
    }

    assert optimize_standard(quotes, _request(accounts)) is None


def test_solver_is_independent_of_mapping_order_and_repeated_runs() -> None:
    quotes = _quotes()
    accounts = {
        source: SourceAccount(balance_cents=10_000, min_stake_cents=100, max_stake_cents=10_000)
        for source in Source
    }
    request = _request(accounts)

    results = [optimize_standard(dict(reversed(tuple(quotes.items()))), request) for _ in range(3)]

    assert results[0] is not None
    assert results[0] == results[1] == results[2]


def test_solver_matches_bounded_bruteforce_primary_and_tiebreak_oracle() -> None:
    quotes = {
        outcome: quote.model_copy(update={"odds": odds})
        for outcome, quote, odds in zip(
            Outcome,
            _quotes().values(),
            (Decimal("12.15"), Decimal("17.59"), Decimal("11.21")),
            strict=True,
        )
    }
    accounts = {
        source: SourceAccount(balance_cents=8, min_stake_cents=1, max_stake_cents=8)
        for source in Source
    }
    request = _request(accounts, budget=10)

    feasible: list[tuple[int, int, tuple[int, int, int]]] = []
    for values in product(range(1, 9), repeat=3):
        if sum(values) > request.budget_cents:
            continue
        gross = tuple(
            payout_cents(stake, quotes[outcome].odds)
            for outcome, stake in zip(Outcome, values, strict=True)
        )
        minimum_profit = min(value - sum(values) for value in gross)
        feasible.append((minimum_profit, sum(values), values))
    expected = max(
        feasible, key=lambda item: (item[0], -item[1], tuple(-value for value in item[2]))
    )
    primary_ties = [item for item in feasible if item[0] == expected[0]]
    assert len(primary_ties) > 1
    assert {item[1] for item in primary_ties} == {9, 10}

    allocation = optimize_standard(quotes, request)

    assert allocation is not None
    actual_values = tuple(allocation.stakes_cents[outcome] for outcome in Outcome)
    assert (
        min(item.standard_profit_cents for item in verify_allocation(quotes, allocation)),
        sum(actual_values),
        actual_values,
    ) == expected


def test_solver_rejects_before_solving_when_model_validation_has_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cp_model.CpModel, "validate", lambda model: "preflight-diagnostic")

    def fail_if_called() -> object:
        raise AssertionError("CP-SAT solve must not run after model validation failure")

    monkeypatch.setattr(solver_module, "_new_solver", fail_if_called)

    with pytest.raises(ValueError, match="preflight-diagnostic"):
        optimize_standard(
            _quotes(),
            _request(
                {
                    source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                    for source in Source
                }
            ),
        )


def test_solver_rejects_oversized_raw_balance_before_model_construction() -> None:
    accounts = {
        source: SourceAccount(balance_cents=10**100, min_stake_cents=100, max_stake_cents=1_000)
        for source in Source
    }

    with pytest.raises(ValueError, match="balance"):
        optimize_standard(_quotes(), _request(accounts))


def test_solver_reports_model_validation_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solver_module, "_validate_model", lambda model: None)
    monkeypatch.setattr(cp_model.CpModel, "validate", lambda model: "diagnostic-123")

    class InvalidSolver:
        parameters = SimpleNamespace(num_search_workers=1, random_seed=0)

        def solve(self, model: cp_model.CpModel) -> cp_model.CpSolverStatus:
            return cp_model.MODEL_INVALID

    monkeypatch.setattr(solver_module, "_new_solver", lambda: InvalidSolver())

    with pytest.raises(ValueError, match="diagnostic-123"):
        optimize_standard(
            _quotes(),
            _request(
                {
                    source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                    for source in Source
                }
            ),
        )


@pytest.mark.parametrize("status", [cp_model.FEASIBLE, cp_model.UNKNOWN, cp_model.MODEL_INVALID])
def test_solver_never_accepts_nonoptimal_statuses(
    monkeypatch: pytest.MonkeyPatch, status: cp_model.CpSolverStatus
) -> None:
    class FakeSolver:
        parameters = SimpleNamespace(num_search_workers=1, random_seed=0)

        def solve(self, model: cp_model.CpModel) -> cp_model.CpSolverStatus:
            return status

    monkeypatch.setattr(solver_module, "_new_solver", lambda: FakeSolver())
    if status is cp_model.MODEL_INVALID:
        monkeypatch.setattr(solver_module, "_validate_model", lambda model: None)
        monkeypatch.setattr(cp_model.CpModel, "validate", lambda model: "invalid diagnostics")
        with pytest.raises(ValueError, match="invalid diagnostics"):
            optimize_standard(
                _quotes(),
                _request(
                    {
                        source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                        for source in Source
                    }
                ),
            )
        return

    result = optimize_standard(
        _quotes(),
        _request(
            {
                source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                for source in Source
            }
        ),
    )

    assert result is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: {
            Outcome.HOME: values[Outcome.DRAW],
            Outcome.DRAW: values[Outcome.HOME],
            Outcome.AWAY: values[Outcome.AWAY],
        },
        lambda values: {Outcome.HOME: values[Outcome.HOME], Outcome.DRAW: values[Outcome.DRAW]},
    ],
)
def test_solver_rejects_quote_mapping_contradictions(mutate) -> None:
    with pytest.raises((TypeError, ValueError)):
        optimize_standard(
            mutate(_quotes()),
            _request(
                {
                    source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                    for source in Source
                }
            ),
        )


def test_solver_rejects_nonopen_quote() -> None:
    quotes = _quotes()
    quotes[Outcome.HOME] = quotes[Outcome.HOME].model_copy(update={"status": "suspended"})

    with pytest.raises(ValueError, match="OPEN"):
        optimize_standard(
            quotes,
            _request(
                {
                    source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000)
                    for source in Source
                }
            ),
        )


def test_solver_returns_none_on_solver_verifier_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = solver_module.verify_allocation

    def disagree(quotes, allocation):
        scenarios = list(original(quotes, allocation))
        scenarios[0] = ScenarioResult(
            outcome=scenarios[0].outcome,
            gross_return_cents=scenarios[0].gross_return_cents + 1,
            standard_profit_cents=scenarios[0].standard_profit_cents,
            bonus_delta_cents=0,
            adjusted_profit_cents=scenarios[0].adjusted_profit_cents,
        )
        return tuple(scenarios)

    monkeypatch.setattr(solver_module, "verify_allocation", disagree)
    accounts = {
        source: SourceAccount(balance_cents=10_000, max_stake_cents=10_000) for source in Source
    }

    assert optimize_standard(_quotes(), _request(accounts)) is None

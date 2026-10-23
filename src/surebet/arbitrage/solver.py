"""Deterministic CP-SAT optimization for a constrained standard allocation."""

from collections.abc import Mapping
from datetime import UTC, timedelta

from ortools.sat.python import cp_model

from surebet.arbitrage.scenarios import evaluate_scenarios
from surebet.arbitrage.verify import verify_allocation
from surebet.bonus.engine import bonus_delta, claimable_bonus
from surebet.domain.enums import Outcome, QuoteStatus, Source
from surebet.domain.models import Allocation, AnalysisRequest, BonusState, Quote
from surebet.money import odds_scaled, payout_cents

ODDS_SCALE = 10_000
# Keep a wide margin below signed int64.  CP-SAT expression arithmetic is
# deliberately guarded more tightly than the pure integer verifier.
_SAFE_EXPRESSION_LIMIT = 10**15
_RANDOM_SEED = 1729


def _guard(value: int, label: str) -> None:
    if abs(value) > _SAFE_EXPRESSION_LIMIT:
        raise ValueError(
            f"unsupported CP-SAT input: {label}={value} exceeds the internal "
            f"safe limit {_SAFE_EXPRESSION_LIMIT}"
        )


def _validate_quotes(
    quotes: Mapping[Outcome, Quote], request: AnalysisRequest
) -> tuple[Quote, ...]:
    if not isinstance(quotes, Mapping):
        raise TypeError("quotes must be a Mapping")
    if set(quotes) != set(Outcome):
        raise ValueError("quotes must contain exactly HOME, DRAW, and AWAY")

    values: list[Quote] = []
    for outcome in Outcome:
        quote = quotes[outcome]
        if not isinstance(quote, Quote):
            raise TypeError(f"quote for {outcome}: expected Quote")
        if quote.outcome is not outcome:
            raise ValueError(f"quote for {outcome}: quote outcome contradicts mapping key")
        if quote.status is not QuoteStatus.OPEN:
            raise ValueError(f"quote for {outcome}: quote is not OPEN")
        if quote.source not in request.accounts:
            raise ValueError(f"quote for {outcome}: no account for source {quote.source}")
        values.append(quote)

    if len({quote.event_id for quote in values}) != 1:
        raise ValueError("all quotes must belong to the same event")
    return tuple(values)


def _new_solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = _RANDOM_SEED
    return solver


def _solve_optimal(model: cp_model.CpModel, stage: str) -> cp_model.CpSolver | None:
    solver = _new_solver()
    status = solver.solve(model)
    if status == cp_model.OPTIMAL:
        return solver
    if status == cp_model.INFEASIBLE:
        return None
    if status == cp_model.MODEL_INVALID:
        try:
            diagnostics = model.validate()
        except Exception as error:  # noqa: BLE001 - preserve validator diagnostics
            diagnostics = f"validation raised {error!r}"
        if not diagnostics:
            diagnostics = "no diagnostics returned by model.validate()"
        raise ValueError(f"CP-SAT model invalid during {stage} solve: {diagnostics}")
    # FEASIBLE, UNKNOWN, and all future non-success statuses are never
    # acceptable because the lexicographic optimum is not established.
    return None


def _validate_model(model: cp_model.CpModel) -> None:
    try:
        validation = model.validate()
    except Exception as error:
        raise ValueError(f"CP-SAT model validation failed before solve: {error}") from error
    if validation:
        raise ValueError(f"CP-SAT model validation failed before solve: {validation}")


def optimize_standard(
    quotes: Mapping[Outcome, Quote], request: AnalysisRequest
) -> Allocation | None:
    """Return the deterministic verified optimum, or ``None`` if unprofitable."""
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest")
    validated_quotes = _validate_quotes(quotes, request)
    _guard(request.budget_cents, "budget_cents")

    stake_upper: dict[Outcome, int] = {}
    gross_upper: dict[Outcome, int] = {}
    scaled_odds: dict[Outcome, int] = {}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        account = request.accounts[quote.source]
        _guard(account.balance_cents, f"{quote.source} balance constraint")
        upper = min(account.max_stake_cents, account.balance_cents, request.budget_cents)
        lower = account.min_stake_cents
        _guard(lower, f"{outcome} minimum stake")
        _guard(upper, f"{outcome} effective stake upper bound")
        if upper < lower:
            return None

        scaled = odds_scaled(quote.odds, ODDS_SCALE)
        _guard(scaled, f"{outcome} scaled odds")
        product_upper = upper * scaled
        _guard(product_upper, f"{outcome} stake_upper * scaled_odds")
        gross_bound = product_upper // ODDS_SCALE
        _guard(gross_bound, f"{outcome} gross payout bound")
        if account.max_payout_cents is not None:
            _guard(account.max_payout_cents, f"{outcome} max payout")
            gross_bound = min(gross_bound, account.max_payout_cents)
        stake_upper[outcome] = upper
        gross_upper[outcome] = gross_bound
        scaled_odds[outcome] = scaled

    total_upper = min(request.budget_cents, sum(stake_upper.values()))
    _guard(total_upper, "total stake upper bound")
    source_upper: dict[Source, int] = {source: 0 for source in Source}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        source_upper[quote.source] += stake_upper[outcome]
    for source, upper in source_upper.items():
        _guard(upper, f"{source} aggregate stake upper bound")

    profit_lower = -total_upper
    profit_upper = max(gross_upper.values())
    _guard(profit_lower, "minimum-profit lower domain")
    _guard(profit_upper, "minimum-profit upper domain")
    for outcome in Outcome:
        _guard(gross_upper[outcome] + total_upper, f"{outcome} gross-total expression range")

    model = cp_model.CpModel()
    stakes: dict[Outcome, cp_model.IntVar] = {}
    gross: dict[Outcome, cp_model.IntVar] = {}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        account = request.accounts[quote.source]
        stake = model.new_int_var(
            account.min_stake_cents,
            stake_upper[outcome],
            f"stake_{outcome.value}",
        )
        stakes[outcome] = stake
        gross_var = model.new_int_var(0, gross_upper[outcome], f"gross_{outcome.value}")
        gross[outcome] = gross_var
        model.add_division_equality(
            gross_var,
            stake * scaled_odds[outcome],
            ODDS_SCALE,
        )
        if account.max_payout_cents is not None:
            model.add(gross_var <= account.max_payout_cents)

    total_stake = sum(stakes.values())
    model.add(total_stake <= request.budget_cents)
    for source in Source:
        source_stakes = [
            stakes[outcome]
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
            if quote.source is source
        ]
        if source_stakes:
            model.add(sum(source_stakes) <= request.accounts[source].balance_cents)

    minimum_profit = model.new_int_var(profit_lower, profit_upper, "minimum_profit_cents")
    for outcome in Outcome:
        model.add(minimum_profit <= gross[outcome] - total_stake)
    _validate_model(model)

    model.maximize(minimum_profit)
    solver = _solve_optimal(model, "minimum standard profit")
    if solver is None:
        return None
    best_minimum_profit = solver.value(minimum_profit)
    _guard(best_minimum_profit, "optimal minimum standard profit")
    model.clear_objective()  # type: ignore[no-untyped-call]
    model.add(minimum_profit == best_minimum_profit)

    _guard(total_upper, "minimum-total-stake objective")
    model.minimize(total_stake)
    solver = _solve_optimal(model, "minimum total stake")
    if solver is None:
        return None
    best_total_stake = solver.value(total_stake)
    _guard(best_total_stake, "optimal total stake")
    model.clear_objective()  # type: ignore[no-untyped-call]
    model.add(total_stake == best_total_stake)

    for outcome in Outcome:
        _guard(stake_upper[outcome], f"{outcome} lexicographic objective")
        model.minimize(stakes[outcome])
        solver = _solve_optimal(model, f"lexicographic {outcome} stake")
        if solver is None:
            return None
        best_stake = solver.value(stakes[outcome])
        _guard(best_stake, f"optimal {outcome} stake")
        model.clear_objective()  # type: ignore[no-untyped-call]
        model.add(stakes[outcome] == best_stake)

    allocation = Allocation(
        stakes_cents={outcome: solver.value(stakes[outcome]) for outcome in Outcome}
    )
    scenarios = verify_allocation(quotes, allocation)
    verified_by_outcome = {scenario.outcome: scenario for scenario in scenarios}
    for outcome in Outcome:
        scenario = verified_by_outcome[outcome]
        solver_gross = solver.value(gross[outcome])
        solver_profit = solver_gross - solver.value(total_stake)
        if (
            solver_gross != scenario.gross_return_cents
            or solver_profit != scenario.standard_profit_cents
        ):
            return None
    if solver.value(minimum_profit) != min(item.standard_profit_cents for item in scenarios):
        return None
    if min(item.standard_profit_cents for item in scenarios) <= 0:
        return None
    return allocation


def _adjusted_source_delta(
    quotes: tuple[Quote, ...],
    allocation: Allocation,
    request: AnalysisRequest,
    winning_outcome: Outcome,
    winning_quote: Quote,
    source: Source,
    *,
    assume_confirmed: bool,
) -> int:
    state = request.accounts[source].bonus
    if state is None:
        return 0
    total_stakes = sum(
        allocation.stakes_cents[outcome]
        for outcome, quote in zip(Outcome, quotes, strict=True)
        if quote.source is source
    )
    winning_gross = payout_cents(allocation.stakes_cents[winning_outcome], winning_quote.odds)
    site_gross = winning_gross if winning_quote.source is source else 0
    site_pnl = site_gross - total_stakes
    return bonus_delta(
        state,
        before_balance_cents=request.accounts[source].balance_cents,
        after_balance_cents=request.accounts[source].balance_cents - total_stakes + site_gross,
        after_loss_cents=max(0, state.loss_before_cents - site_pnl),
        now=request.bonus_evaluation_at,  # type: ignore[arg-type]
        assume_confirmed=assume_confirmed,
    )


def optimize_adjusted(
    quotes: Mapping[Outcome, Quote],
    request: AnalysisRequest,
    *,
    assume_confirmed: bool = False,
) -> Allocation | None:
    """Return the deterministic max-min allocation including settled bonus deltas."""
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest")
    if type(assume_confirmed) is not bool:
        raise TypeError("assume_confirmed must be a bool")
    validated_quotes = _validate_quotes(quotes, request)
    instant = request.bonus_evaluation_at
    if instant is None:
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("bonus_evaluation_at must be timezone-aware")
    if instant.astimezone(UTC) < request.now.astimezone(UTC):
        raise ValueError("bonus_evaluation_at must not be earlier than now")

    participating_sources = {
        quote.source
        for quote in validated_quotes
        if request.accounts[quote.source].bonus is not None
    }
    if not participating_sources:
        return None
    participating_source_order = tuple(
        source for source in Source if source in participating_sources
    )
    for source in participating_source_order:
        state = request.accounts[source].bonus
        assert state is not None
        if instant.astimezone(UTC) < state.period_anchor.astimezone(UTC):
            raise ValueError(
                "bonus_evaluation_at must not be before an assigned bonus period_anchor"
            )

    _guard(request.budget_cents, "budget_cents")
    stake_upper: dict[Outcome, int] = {}
    gross_upper: dict[Outcome, int] = {}
    scaled_odds: dict[Outcome, int] = {}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        account = request.accounts[quote.source]
        _guard(account.balance_cents, f"{quote.source} balance constraint")
        upper = min(account.max_stake_cents, account.balance_cents, request.budget_cents)
        lower = account.min_stake_cents
        _guard(lower, f"{outcome} minimum stake")
        _guard(upper, f"{outcome} effective stake upper bound")
        if upper < lower:
            return None
        scaled = odds_scaled(quote.odds, ODDS_SCALE)
        _guard(scaled, f"{outcome} scaled odds")
        product_upper = upper * scaled
        _guard(product_upper, f"{outcome} stake_upper * scaled_odds")
        gross_bound = product_upper // ODDS_SCALE
        _guard(gross_bound, f"{outcome} gross payout bound")
        if account.max_payout_cents is not None:
            _guard(account.max_payout_cents, f"{outcome} max payout")
            gross_bound = min(gross_bound, account.max_payout_cents)
        stake_upper[outcome] = upper
        gross_upper[outcome] = gross_bound
        scaled_odds[outcome] = scaled
        _guard(gross_bound + request.budget_cents, f"{outcome} gross-total expression range")

    total_upper = min(request.budget_cents, sum(stake_upper.values()))
    _guard(total_upper, "total stake upper bound")
    source_upper: dict[Source, int] = {source: 0 for source in Source}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        source_upper[quote.source] += stake_upper[outcome]
    for source, upper in source_upper.items():
        _guard(upper, f"{source} aggregate stake upper bound")

    source_states: dict[Source, BonusState] = {}
    for source in participating_source_order:
        state = request.accounts[source].bonus
        assert state is not None
        source_states[source] = state
    source_stake_vars_upper = {
        source: source_upper[source] for source in participating_source_order
    }
    for source, state in source_states.items():
        assert state is not None
        _guard(state.loss_before_cents, f"{source} loss_before")
        loss_upper = state.loss_before_cents + source_stake_vars_upper[source]
        _guard(loss_upper, f"{source} loss_after upper bound")
        balance_upper = request.accounts[source].balance_cents + max(
            gross_upper[outcome]
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
            if quote.source is source
        )
        _guard(balance_upper, f"{source} balance_after upper bound")
        baseline = claimable_bonus(
            state,
            balance_cents=request.accounts[source].balance_cents,
            loss_cents=state.loss_before_cents,
            now=instant,
            assume_confirmed=assume_confirmed,
        ).amount_cents
        _guard(baseline, f"{source} bonus baseline")
        _guard(loss_upper // 10, f"{source} bonus_after upper bound")
        _guard(baseline + loss_upper // 10, f"{source} bonus delta range")

    standard_lower = -total_upper
    standard_upper = max(gross_upper.values())
    bonus_lower = -sum(
        [
            claimable_bonus(
                state,
                balance_cents=request.accounts[source].balance_cents,
                loss_cents=state.loss_before_cents,
                now=instant,
                assume_confirmed=assume_confirmed,
            ).amount_cents
            for source, state in source_states.items()
        ]
    )
    adjusted_lower = standard_lower + bonus_lower
    adjusted_upper = standard_upper + sum(
        (state.loss_before_cents + source_stake_vars_upper[source]) // 10
        for source, state in source_states.items()
    )
    for value, label in (
        (standard_lower, "minimum standard lower domain"),
        (standard_upper, "minimum standard upper domain"),
        (adjusted_lower, "minimum adjusted lower domain"),
        (adjusted_upper, "minimum adjusted upper domain"),
    ):
        _guard(value, label)

    model = cp_model.CpModel()
    stakes: dict[Outcome, cp_model.IntVar] = {}
    gross: dict[Outcome, cp_model.IntVar] = {}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        account = request.accounts[quote.source]
        stake = model.new_int_var(
            account.min_stake_cents, stake_upper[outcome], f"stake_{outcome.value}"
        )
        gross_var = model.new_int_var(0, gross_upper[outcome], f"gross_{outcome.value}")
        stakes[outcome] = stake
        gross[outcome] = gross_var
        model.add_division_equality(gross_var, stake * scaled_odds[outcome], ODDS_SCALE)
        if account.max_payout_cents is not None:
            model.add(gross_var <= account.max_payout_cents)

    total_stake = sum(stakes.values())
    model.add(total_stake <= request.budget_cents)
    for source in Source:
        source_stakes = [
            stakes[outcome]
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
            if quote.source is source
        ]
        if source_stakes:
            model.add(sum(source_stakes) <= request.accounts[source].balance_cents)

    source_deltas: dict[tuple[Outcome, Source], cp_model.IntVar] = {}
    for source in participating_source_order:
        state = source_states[source]
        assert state is not None
        source_stakes = [
            stakes[outcome]
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
            if quote.source is source
        ]
        site_stakes = model.new_int_var(
            0, source_stake_vars_upper[source], f"stakes_{source.value}"
        )
        model.add(site_stakes == sum(source_stakes))
        fixed_reasons = (
            state.net_deposit_cents < 10_000
            or instant.astimezone(UTC) - state.period_anchor.astimezone(UTC) >= timedelta(hours=72)
            or state.withdrawals_exceed_deposits
            or state.unrelated_open_bets
            or (not state.requirements_confirmed and not assume_confirmed)
        )
        baseline = claimable_bonus(
            state,
            balance_cents=request.accounts[source].balance_cents,
            loss_cents=state.loss_before_cents,
            now=instant,
            assume_confirmed=assume_confirmed,
        ).amount_cents
        loss_upper = state.loss_before_cents + source_stake_vars_upper[source]
        balance_upper = request.accounts[source].balance_cents + max(
            gross_upper[outcome]
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
            if quote.source is source
        )
        for winning_outcome, winning_quote in zip(Outcome, validated_quotes, strict=True):
            site_gross = gross[winning_outcome] if winning_quote.source is source else 0
            balance_after = model.new_int_var(
                0, balance_upper, f"balance_{source.value}_{winning_outcome.value}"
            )
            model.add(
                balance_after == request.accounts[source].balance_cents - site_stakes + site_gross
            )
            loss_raw = model.new_int_var(
                -max(gross_upper.values()),
                loss_upper,
                f"loss_raw_{source.value}_{winning_outcome.value}",
            )
            model.add(loss_raw == state.loss_before_cents + site_stakes - site_gross)
            loss_after = model.new_int_var(
                0, loss_upper, f"loss_{source.value}_{winning_outcome.value}"
            )
            model.add_max_equality(loss_after, [0, loss_raw])
            eligible = model.new_bool_var(f"eligible_{source.value}_{winning_outcome.value}")
            balance_ok = model.new_bool_var(f"balance_ok_{source.value}_{winning_outcome.value}")
            loss_ok = model.new_bool_var(f"loss_ok_{source.value}_{winning_outcome.value}")
            model.add(balance_after <= 1_999).only_enforce_if(balance_ok)
            model.add(balance_after >= 2_000).only_enforce_if(balance_ok.negated())
            model.add(loss_after >= 10_000).only_enforce_if(loss_ok)
            model.add(loss_after <= 9_999).only_enforce_if(loss_ok.negated())
            bonus_after = model.new_int_var(
                0, loss_upper // 10, f"bonus_{source.value}_{winning_outcome.value}"
            )
            if fixed_reasons:
                model.add(eligible == 0)
            else:
                model.add_bool_and([balance_ok, loss_ok]).only_enforce_if(eligible)
                model.add_bool_or([balance_ok.negated(), loss_ok.negated(), eligible])
            model.add_division_equality(bonus_after, loss_after, 10).only_enforce_if(eligible)
            model.add(bonus_after == 0).only_enforce_if(eligible.negated())
            delta = model.new_int_var(
                -baseline,
                loss_upper // 10 - baseline,
                f"delta_{source.value}_{winning_outcome.value}",
            )
            model.add(delta == bonus_after - baseline)
            source_deltas[(winning_outcome, source)] = delta

    minimum_adjusted = model.new_int_var(
        adjusted_lower, adjusted_upper, "minimum_adjusted_profit_cents"
    )
    adjusted: dict[Outcome, cp_model.IntVar] = {}
    for outcome in Outcome:
        deltas = [source_deltas[(outcome, source)] for source in participating_source_order]
        adjusted_var = model.new_int_var(
            adjusted_lower, adjusted_upper, f"adjusted_{outcome.value}"
        )
        model.add(adjusted_var == gross[outcome] - total_stake + sum(deltas))
        adjusted[outcome] = adjusted_var
        model.add(minimum_adjusted <= adjusted_var)
    _validate_model(model)

    model.maximize(minimum_adjusted)
    solver = _solve_optimal(model, "minimum adjusted profit")
    if solver is None:
        return None
    best_minimum = solver.value(minimum_adjusted)
    _guard(best_minimum, "optimal minimum adjusted profit")
    model.clear_objective()  # type: ignore[no-untyped-call]
    model.add(minimum_adjusted == best_minimum)
    model.minimize(total_stake)
    solver = _solve_optimal(model, "minimum total stake")
    if solver is None:
        return None
    best_total_stake = solver.value(total_stake)
    _guard(best_total_stake, "optimal adjusted total stake")
    model.clear_objective()  # type: ignore[no-untyped-call]
    model.add(total_stake == best_total_stake)
    for outcome in Outcome:
        model.minimize(stakes[outcome])
        solver = _solve_optimal(model, f"adjusted lexicographic {outcome} stake")
        if solver is None:
            return None
        best_stake = solver.value(stakes[outcome])
        _guard(best_stake, f"optimal adjusted {outcome} stake")
        model.clear_objective()  # type: ignore[no-untyped-call]
        model.add(stakes[outcome] == best_stake)

    allocation = Allocation(
        stakes_cents={outcome: solver.value(stakes[outcome]) for outcome in Outcome}
    )
    if sum(allocation.stakes_cents.values()) > request.budget_cents:
        return None
    aggregate_stakes = {source: 0 for source in request.accounts}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        stake_value = allocation.stakes_cents[outcome]
        account = request.accounts[quote.source]
        if (
            not account.min_stake_cents
            <= stake_value
            <= min(account.max_stake_cents, account.balance_cents)
        ):
            return None
        aggregate_stakes[quote.source] += stake_value
        gross_value = payout_cents(stake_value, quote.odds)
        if account.max_payout_cents is not None and gross_value > account.max_payout_cents:
            return None
    if any(
        aggregate_stakes[source] > request.accounts[source].balance_cents
        for source in request.accounts
    ):
        return None
    scenarios = evaluate_scenarios(quotes, allocation, request, assume_confirmed=assume_confirmed)
    scenario_by_outcome = {scenario.outcome: scenario for scenario in scenarios}
    for outcome in Outcome:
        scenario = scenario_by_outcome[outcome]
        gross_value = payout_cents(allocation.stakes_cents[outcome], quotes[outcome].odds)
        if solver.value(gross[outcome]) != gross_value:
            return None
        if solver.value(adjusted[outcome]) != scenario.adjusted_profit_cents:
            return None
        if solver.value(gross[outcome]) != scenario.gross_return_cents:
            return None
        if (
            solver.value(gross[outcome]) - solver.value(total_stake)
            != scenario.standard_profit_cents
        ):
            return None
        independent_delta = sum(
            _adjusted_source_delta(
                validated_quotes,
                allocation,
                request,
                outcome,
                quotes[outcome],
                source,
                assume_confirmed=assume_confirmed,
            )
            for source in participating_source_order
        )
        if independent_delta != scenario.bonus_delta_cents:
            return None
        for source in participating_source_order:
            if solver.value(source_deltas[(outcome, source)]) != _adjusted_source_delta(
                validated_quotes,
                allocation,
                request,
                outcome,
                quotes[outcome],
                source,
                assume_confirmed=assume_confirmed,
            ):
                return None
    if solver.value(minimum_adjusted) != min(item.adjusted_profit_cents for item in scenarios):
        return None
    if min(item.adjusted_profit_cents for item in scenarios) <= 0:
        return None
    return allocation

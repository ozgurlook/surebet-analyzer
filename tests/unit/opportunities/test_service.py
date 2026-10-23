from datetime import UTC, datetime, timedelta
from fractions import Fraction
from itertools import product
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from surebet.arbitrage.scenarios import evaluate_scenarios as real_evaluate_scenarios
from surebet.domain.enums import OpportunityClass, Outcome, QuoteStatus, Source
from surebet.domain.models import (
    Allocation,
    AnalysisRequest,
    BonusState,
    Opportunity,
    Quote,
    ScenarioResult,
    SourceAccount,
)
from surebet.money import payout_cents
from surebet.opportunities import service
from surebet.opportunities.service import find_opportunities

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _request(*, evaluation_at: datetime | None = None) -> AnalysisRequest:
    return AnalysisRequest(
        budget_cents=300,
        now=NOW,
        bonus_evaluation_at=evaluation_at,
        stale_after_seconds=75,
        accounts={
            Source.SOURCE_A: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
            Source.SOURCE_B: SourceAccount(
                balance_cents=200,
                max_stake_cents=100_000,
                bonus=BonusState(
                    loss_before_cents=9_950,
                    net_deposit_cents=10_000,
                    period_anchor=NOW,
                ),
            ),
            Source.SOURCE_C: SourceAccount(
                balance_cents=200,
                max_stake_cents=100_000,
                bonus=BonusState(
                    loss_before_cents=9_950,
                    net_deposit_cents=10_000,
                    period_anchor=NOW,
                ),
            ),
        },
    )


def _quotes(
    *, observed_at: datetime = NOW, odds: tuple[str, str, str] = ("2.90", "3.60", "3.10")
) -> tuple[Quote, ...]:
    return tuple(
        Quote(
            source=source,
            event_id="event-1",
            outcome=outcome,
            odds=price,
            observed_at=observed_at,
        )
        for outcome, source, price in zip(
            Outcome, (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C), odds, strict=True
        )
    )


def _standard_request() -> AnalysisRequest:
    return _request().model_copy(
        update={
            "budget_cents": 10_000,
            "accounts": {
                source: SourceAccount(balance_cents=100_000, max_stake_cents=100_000)
                for source in Source
            },
        }
    )


def _all_source_quotes() -> tuple[Quote, ...]:
    return tuple(
        Quote(
            source=source,
            event_id="event-1",
            outcome=outcome,
            odds="2.90",
            observed_at=NOW,
        )
        for outcome in Outcome
        for source in Source
    )


def _allocation() -> Allocation:
    return Allocation(stakes_cents={outcome: 100 for outcome in Outcome})


def _opportunity(
    classification: OpportunityClass = OpportunityClass.STANDARD,
    *,
    event_id: str = "event-1",
    sources: tuple[Source, Source, Source] = (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C),
    observed_at: tuple[datetime, datetime, datetime] = (NOW, NOW, NOW),
    odds: tuple[str, str, str] = ("2.90", "3.60", "3.10"),
    stakes: tuple[int, int, int] = (3_000, 2_400, 2_800),
    bonus_deltas: tuple[int, int, int] = (0, 0, 0),
) -> Opportunity:
    quotes = {
        outcome: Quote(
            source=source,
            event_id=event_id,
            outcome=outcome,
            odds=price,
            observed_at=instant,
        )
        for outcome, source, instant, price in zip(Outcome, sources, observed_at, odds, strict=True)
    }
    allocation = Allocation(stakes_cents=dict(zip(Outcome, stakes, strict=True)))
    total_stake = sum(stakes)
    scenarios = tuple(
        ScenarioResult(
            outcome=outcome,
            gross_return_cents=payout_cents(stakes[index], quotes[outcome].odds),
            standard_profit_cents=payout_cents(stakes[index], quotes[outcome].odds) - total_stake,
            bonus_delta_cents=bonus_deltas[index],
            adjusted_profit_cents=(
                payout_cents(stakes[index], quotes[outcome].odds)
                - total_stake
                + bonus_deltas[index]
            ),
        )
        for index, outcome in enumerate(Outcome)
    )
    return Opportunity(
        classification=classification,
        quotes=quotes,
        allocation=allocation,
        scenarios=scenarios,
        minimum_standard_profit_cents=min(item.standard_profit_cents for item in scenarios),
        minimum_adjusted_profit_cents=min(item.adjusted_profit_cents for item in scenarios),
    )


def test_quote_older_than_stale_window_cannot_create_opportunity() -> None:
    quotes = _quotes(observed_at=NOW - timedelta(seconds=76))

    assert find_opportunities(quotes, _request()) == ()


def test_verified_assignment_is_classified_standard() -> None:
    result = find_opportunities(_quotes(), _standard_request())

    assert result
    assert result[0].classification is OpportunityClass.STANDARD


def test_candidate_profitable_only_in_strict_confirmed_bonus_mode_is_qualified() -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    result = find_opportunities(
        _quotes(odds=("1.50", "2.00", "2.00")),
        request,
    )

    assert result
    assert result[0].classification is OpportunityClass.BONUS_QUALIFIED


def test_candidate_profitable_only_when_confirmation_is_assumed_is_conditional() -> None:
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    quotes = _quotes(odds=("1.50", "2.00", "2.00"))
    request = request.model_copy(
        update={
            "accounts": {
                **request.accounts,
                Source.SOURCE_B: SourceAccount(
                    balance_cents=200,
                    max_stake_cents=100_000,
                    bonus=BonusState(
                        loss_before_cents=9_950,
                        net_deposit_cents=10_000,
                        period_anchor=NOW,
                        requirements_confirmed=False,
                    ),
                ),
            }
        }
    )

    result = find_opportunities(quotes, request)

    assert result
    assert result[0].classification is OpportunityClass.BONUS_CONDITIONAL


def test_newer_closed_snapshot_supersedes_older_open_snapshot() -> None:
    quotes = list(_quotes())
    quotes.append(
        quotes[0].model_copy(
            update={"observed_at": NOW + timedelta(microseconds=1), "status": QuoteStatus.CLOSED}
        )
    )

    assert find_opportunities(quotes, _request()) == ()


def test_newer_suspended_snapshot_supersedes_older_open_snapshot() -> None:
    quotes = list(_quotes())
    quotes.append(
        quotes[0].model_copy(
            update={"observed_at": NOW + timedelta(microseconds=1), "status": QuoteStatus.SUSPENDED}
        )
    )

    assert find_opportunities(quotes, _request()) == ()


def test_future_observation_is_excluded() -> None:
    quotes = list(_quotes())
    quotes[0] = quotes[0].model_copy(update={"observed_at": NOW + timedelta(microseconds=1)})

    assert find_opportunities(quotes, _standard_request()) == ()


def test_latest_future_snapshot_shadows_older_current_snapshot() -> None:
    quotes = list(_quotes())
    quotes.append(quotes[0].model_copy(update={"observed_at": NOW + timedelta(microseconds=1)}))

    assert find_opportunities(quotes, _standard_request()) == ()


def test_newer_stale_observation_does_not_resurrect_an_older_snapshot() -> None:
    quotes = list(_quotes())
    quotes[0] = quotes[0].model_copy(update={"observed_at": NOW - timedelta(seconds=100)})
    quotes.append(quotes[0].model_copy(update={"observed_at": NOW - timedelta(seconds=76)}))

    assert find_opportunities(quotes, _standard_request()) == ()


def test_latest_conflict_fails_closed_but_alternate_source_continues() -> None:
    conflict_time = NOW - timedelta(seconds=1)
    quotes = list(_quotes())
    quotes[0] = quotes[0].model_copy(
        update={"observed_at": conflict_time - timedelta(microseconds=1)}
    )
    quotes.extend(
        (
            Quote(
                source=Source.SOURCE_A,
                event_id="event-1",
                outcome=Outcome.HOME,
                odds="2.90",
                observed_at=conflict_time,
            ),
            Quote(
                source=Source.SOURCE_A,
                event_id="event-1",
                outcome=Outcome.HOME,
                odds="2.91",
                observed_at=conflict_time,
            ),
            Quote(
                source=Source.SOURCE_B,
                event_id="event-1",
                outcome=Outcome.HOME,
                odds="3.00",
                observed_at=conflict_time,
            ),
        )
    )

    result = find_opportunities(quotes, _standard_request())

    assert result
    assert all(item.quotes[Outcome.HOME].source is Source.SOURCE_B for item in result)


def test_latest_same_instant_status_conflict_fails_closed() -> None:
    quotes = list(_quotes())
    quotes.append(
        quotes[0].model_copy(
            update={"status": QuoteStatus.SUSPENDED, "observed_at": quotes[0].observed_at}
        )
    )

    assert find_opportunities(quotes, _standard_request()) == ()


def test_elapsed_age_across_dst_transition_uses_utc_instants() -> None:
    observed = datetime(2026, 3, 8, 1, 59, 59, tzinfo=ZoneInfo("America/New_York"))
    now = datetime(2026, 3, 8, 3, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    request = _standard_request().model_copy(update={"now": now})

    assert find_opportunities(_quotes(observed_at=observed), request)


def test_empty_and_missing_outcome_return_no_data() -> None:
    assert find_opportunities((), _request()) == ()
    assert find_opportunities(_quotes()[:2], _standard_request()) == ()


def test_wrong_request_sequence_and_element_types_are_rejected() -> None:
    with pytest.raises(TypeError):
        find_opportunities(_quotes(), object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        find_opportunities(iter(_quotes()), _request())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        find_opportunities([*_quotes(), object()], _request())  # type: ignore[list-item]


def test_exact_stale_boundary_is_usable_but_one_microsecond_older_is_not() -> None:
    request = _request().model_copy(
        update={
            "budget_cents": 10_000,
            "accounts": {
                source: SourceAccount(balance_cents=100_000, max_stake_cents=100_000)
                for source in Source
            },
        }
    )

    assert find_opportunities(_quotes(observed_at=NOW - timedelta(seconds=75)), request)
    assert (
        find_opportunities(
            _quotes(observed_at=NOW - timedelta(seconds=75, microseconds=1)), request
        )
        == ()
    )


def test_same_instant_offset_and_decimal_scale_are_canonical_and_input_order_independent() -> None:
    request = _standard_request()
    offset_now = datetime(2026, 8, 24, 15, tzinfo=ZoneInfo("Europe/Istanbul"))
    duplicate = Quote(
        source=Source.SOURCE_A,
        event_id="event-1",
        outcome=Outcome.HOME,
        odds="2.9000",
        observed_at=offset_now,
    )
    first = find_opportunities((*_quotes(), duplicate), request)
    second = find_opportunities((duplicate, *_quotes()), request)

    assert len(first) == len(second) == 1
    canonical = first[0].quotes[Outcome.HOME]
    assert first == second
    assert canonical.odds.as_tuple().digits == (2, 9)
    assert canonical.odds.as_tuple().exponent == -1
    assert canonical.observed_at == NOW
    assert canonical.observed_at.tzinfo is UTC
    assert canonical.observed_at.utcoffset() == timedelta(0)


def test_mixed_events_and_wrong_input_types_are_rejected() -> None:
    with pytest.raises(ValueError):
        find_opportunities(
            (*_quotes(), _quotes()[0].model_copy(update={"event_id": "event-2"})),
            _request(),
        )
    with pytest.raises(TypeError):
        find_opportunities([*_quotes(), "not a quote"], _request())  # type: ignore[list-item]


def test_shared_source_balance_is_checked_across_all_three_outcomes() -> None:
    quotes = tuple(
        Quote(
            source=Source.SOURCE_B,
            event_id="event-1",
            outcome=outcome,
            odds="4.00",
            observed_at=NOW,
        )
        for outcome in Outcome
    )
    balance_200 = _request().model_copy(
        update={
            "accounts": {
                Source.SOURCE_A: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
                Source.SOURCE_B: SourceAccount(balance_cents=200, max_stake_cents=100_000),
                Source.SOURCE_C: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
            }
        }
    )
    balance_300 = balance_200.model_copy(
        update={
            "accounts": {
                **balance_200.accounts,
                Source.SOURCE_B: SourceAccount(balance_cents=300, max_stake_cents=100_000),
            }
        }
    )

    assert find_opportunities(quotes, balance_200) == ()
    accepted = find_opportunities(quotes, balance_300)
    assert accepted
    assert accepted[0].classification is OpportunityClass.STANDARD


def test_all_assignments_are_enumerated_in_outcome_then_source_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Source, Source, Source]] = []

    def record(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> None:
        del request
        calls.append(tuple(assignment[outcome].source for outcome in Outcome))

    monkeypatch.setattr(service, "_classify_assignment", record)
    find_opportunities(_all_source_quotes(), _standard_request())

    expected = list(product(Source, repeat=3))
    assert len(calls) == len(expected)
    assert calls[0] == (Source.SOURCE_A, Source.SOURCE_A, Source.SOURCE_A)
    assert calls == expected


def test_reversed_input_has_same_enumeration_and_does_not_mutate_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_calls: list[tuple[Source, Source, Source]] = []
    second_calls: list[tuple[Source, Source, Source]] = []
    original = list(_all_source_quotes())

    def first(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> None:
        del request
        first_calls.append(tuple(assignment[outcome].source for outcome in Outcome))

    def second(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> None:
        del request
        second_calls.append(tuple(assignment[outcome].source for outcome in Outcome))

    monkeypatch.setattr(service, "_classify_assignment", first)
    find_opportunities(original, _standard_request())
    assert original == list(_all_source_quotes())
    monkeypatch.setattr(service, "_classify_assignment", second)
    find_opportunities(tuple(reversed(original)), _standard_request())

    assert first_calls == second_calls


def test_request_account_mapping_order_does_not_change_results() -> None:
    request = _standard_request()
    reversed_accounts = dict(reversed(tuple(request.accounts.items())))
    reordered = request.model_copy(update={"accounts": reversed_accounts})

    assert find_opportunities(_quotes(), request) == find_opportunities(_quotes(), reordered)


def test_multi_opportunity_output_is_identical_for_reversed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def classify(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> Opportunity:
        del request
        sources = tuple(assignment[outcome].source for outcome in Outcome)
        return _opportunity(sources=sources)

    monkeypatch.setattr(service, "_classify_assignment", classify)
    quotes = _all_source_quotes()

    forward = find_opportunities(quotes, _standard_request())
    reverse = find_opportunities(tuple(reversed(quotes)), _standard_request())

    expected_len = len(list(product(Source, repeat=3)))
    assert len(forward) == len(reverse) == expected_len
    assert forward == reverse


def test_standard_result_has_precedence_and_uses_strict_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocation = Allocation(
        stakes_cents={Outcome.HOME: 3_000, Outcome.DRAW: 2_400, Outcome.AWAY: 2_800}
    )
    traces: list[tuple[str, bool | None]] = []
    quotes = {quote.outcome: quote for quote in _quotes()}

    def standard(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> Allocation:
        del assignment, request
        traces.append(("standard", None))
        return allocation

    def adjusted(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("adjusted optimization must not run after standard success")

    def evaluate(
        assignment: dict[Outcome, Quote],
        selected: Allocation,
        request: AnalysisRequest,
        *,
        assume_confirmed: bool = False,
    ) -> tuple[ScenarioResult, ...]:
        traces.append(("evaluate", assume_confirmed))
        return real_evaluate_scenarios(
            assignment, selected, request, assume_confirmed=assume_confirmed
        )

    monkeypatch.setattr(service, "optimize_standard", standard)
    monkeypatch.setattr(service, "optimize_adjusted", adjusted)
    monkeypatch.setattr(service, "evaluate_scenarios", evaluate)

    result = find_opportunities(tuple(quotes.values()), _standard_request())

    assert result[0].classification is OpportunityClass.STANDARD
    assert traces == [("standard", None), ("evaluate", False)]


def test_strict_adjusted_is_evaluated_before_assumed_and_modes_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocation = _allocation()
    traces: list[tuple[str, bool | None]] = []
    quotes = {quote.outcome: quote for quote in _quotes(odds=("1.50", "2.00", "2.00"))}

    def standard(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> None:
        del assignment, request
        traces.append(("standard", None))

    def adjusted(
        assignment: dict[Outcome, Quote],
        request: AnalysisRequest,
        *,
        assume_confirmed: bool = False,
    ) -> Allocation:
        del assignment, request
        traces.append(("adjusted", assume_confirmed))
        return allocation

    def evaluate(
        assignment: dict[Outcome, Quote],
        selected: Allocation,
        request: AnalysisRequest,
        *,
        assume_confirmed: bool = False,
    ) -> tuple[ScenarioResult, ...]:
        traces.append(("evaluate", assume_confirmed))
        return real_evaluate_scenarios(
            assignment, selected, request, assume_confirmed=assume_confirmed
        )

    monkeypatch.setattr(service, "optimize_standard", standard)
    monkeypatch.setattr(service, "optimize_adjusted", adjusted)
    monkeypatch.setattr(service, "evaluate_scenarios", evaluate)

    result = find_opportunities(
        tuple(quotes.values()), _request(evaluation_at=NOW + timedelta(hours=1))
    )

    assert result[0].classification is OpportunityClass.BONUS_QUALIFIED
    assert traces == [
        ("standard", None),
        ("adjusted", False),
        ("evaluate", False),
    ]


def test_conditional_path_matches_assumed_optimizer_and_evaluator_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocation = _allocation()
    traces: list[tuple[str, bool | None]] = []
    quotes = {quote.outcome: quote for quote in _quotes(odds=("1.50", "2.00", "2.00"))}
    base_request = _request(evaluation_at=NOW + timedelta(hours=1))
    request = base_request.model_copy(
        update={
            "accounts": {
                **base_request.accounts,
                Source.SOURCE_B: SourceAccount(
                    balance_cents=200,
                    max_stake_cents=100_000,
                    bonus=BonusState(
                        loss_before_cents=9_950,
                        net_deposit_cents=10_000,
                        period_anchor=NOW,
                        requirements_confirmed=False,
                    ),
                ),
            }
        }
    )

    def standard(assignment: dict[Outcome, Quote], request: AnalysisRequest) -> None:
        del assignment, request
        traces.append(("standard", None))

    def adjusted(
        assignment: dict[Outcome, Quote],
        request: AnalysisRequest,
        *,
        assume_confirmed: bool = False,
    ) -> Allocation:
        del assignment, request
        traces.append(("adjusted", assume_confirmed))
        return allocation

    def evaluate(
        assignment: dict[Outcome, Quote],
        selected: Allocation,
        request: AnalysisRequest,
        *,
        assume_confirmed: bool = False,
    ) -> tuple[ScenarioResult, ...]:
        traces.append(("evaluate", assume_confirmed))
        return real_evaluate_scenarios(
            assignment, selected, request, assume_confirmed=assume_confirmed
        )

    monkeypatch.setattr(service, "optimize_standard", standard)
    monkeypatch.setattr(service, "optimize_adjusted", adjusted)
    monkeypatch.setattr(service, "evaluate_scenarios", evaluate)

    result = find_opportunities(tuple(quotes.values()), request)

    assert result[0].classification is OpportunityClass.BONUS_CONDITIONAL
    assert traces == [
        ("standard", None),
        ("adjusted", False),
        ("evaluate", False),
        ("adjusted", True),
        ("evaluate", True),
    ]


def test_unassigned_unconfirmed_account_does_not_downgrade_strict_opportunity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocation = _allocation()
    quotes = tuple(
        quote.model_copy(update={"source": Source.SOURCE_C})
        for quote in _quotes(odds=("1.50", "2.00", "2.00"))
    )
    request = _request(evaluation_at=NOW + timedelta(hours=1))
    request = request.model_copy(
        update={
            "accounts": {
                **request.accounts,
                Source.SOURCE_B: SourceAccount(
                    balance_cents=200,
                    max_stake_cents=100_000,
                    bonus=BonusState(
                        loss_before_cents=9_950,
                        net_deposit_cents=10_000,
                        period_anchor=NOW,
                        requirements_confirmed=False,
                    ),
                ),
            }
        }
    )
    confirmed_request = request.model_copy(
        update={
            "accounts": {
                **request.accounts,
                Source.SOURCE_B: SourceAccount(
                    balance_cents=200,
                    max_stake_cents=100_000,
                    bonus=BonusState(
                        loss_before_cents=9_950,
                        net_deposit_cents=10_000,
                        period_anchor=NOW,
                        requirements_confirmed=True,
                    ),
                ),
            }
        }
    )

    monkeypatch.setattr(service, "optimize_standard", lambda *_args: None)
    monkeypatch.setattr(service, "optimize_adjusted", lambda *_args, **_kwargs: allocation)
    result = find_opportunities(quotes, request)
    confirmed = find_opportunities(quotes, confirmed_request)

    assert result[0].classification is OpportunityClass.BONUS_QUALIFIED
    assert result[0].scenarios == confirmed[0].scenarios


def test_missing_or_expired_bonus_evaluation_time_is_fail_closed() -> None:
    quotes = _quotes(odds=("1.50", "2.00", "2.00"))

    assert find_opportunities(quotes, _request()) == ()
    assert (
        find_opportunities(
            quotes,
            _request(evaluation_at=NOW + timedelta(hours=72)),
        )
        == ()
    )


def test_solver_none_is_no_opportunity_and_internal_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "optimize_standard", lambda *_args: None)
    monkeypatch.setattr(service, "optimize_adjusted", lambda *_args, **_kwargs: None)

    assert find_opportunities(_quotes(), _request()) == ()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("solver contract failure")

    monkeypatch.setattr(service, "optimize_standard", broken)
    with pytest.raises(RuntimeError, match="solver contract failure"):
        find_opportunities(_quotes(), _request())


def test_opportunity_uses_exact_scenarios_in_order_and_declares_their_minima(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocation = Allocation(
        stakes_cents={Outcome.HOME: 3_000, Outcome.DRAW: 2_400, Outcome.AWAY: 2_800}
    )
    quotes = tuple(_quotes())
    scenarios = real_evaluate_scenarios(
        {quote.outcome: quote for quote in quotes}, allocation, _standard_request()
    )

    monkeypatch.setattr(service, "optimize_standard", lambda *_args: allocation)
    monkeypatch.setattr(
        service, "optimize_adjusted", lambda *_args, **_kwargs: pytest.fail("unreachable")
    )
    monkeypatch.setattr(service, "evaluate_scenarios", lambda *_args, **_kwargs: scenarios)

    result = find_opportunities(quotes, _standard_request())

    assert result[0].scenarios == scenarios
    assert tuple(item.outcome for item in result[0].scenarios) == tuple(Outcome)
    assert result[0].minimum_standard_profit_cents == min(
        item.standard_profit_cents for item in scenarios
    )
    assert result[0].minimum_adjusted_profit_cents == min(
        item.adjusted_profit_cents for item in scenarios
    )
    with pytest.raises(ValidationError):
        result[0].classification = OpportunityClass.BONUS_QUALIFIED  # type: ignore[misc]


def test_sort_key_contains_all_fields_with_exact_ratio_and_epoch_microseconds() -> None:
    opportunity = _opportunity(
        event_id="event-9",
        observed_at=(NOW - timedelta(microseconds=2), NOW, NOW + timedelta(microseconds=3)),
    )
    elapsed = (NOW - timedelta(microseconds=2)) - datetime(1970, 1, 1, tzinfo=UTC)
    epoch_microseconds = (
        elapsed.days * 86_400 * 1_000_000 + elapsed.seconds * 1_000_000 + elapsed.microseconds
    )
    key = service._sort_key(opportunity)

    assert key == (
        0,
        -440,
        -Fraction(440, 8_200),
        -epoch_microseconds,
        8_200,
        "event-9",
        (0, 1, 2),
        (-29_000, -36_000, -31_000),
        tuple(
            -service._epoch_microseconds(instant)
            for instant in (NOW - timedelta(microseconds=2), NOW, NOW + timedelta(microseconds=3))
        ),
        (3_000, 2_400, 2_800),
    )

    bonus = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("1.50", "2.00", "2.00"),
        stakes=(100, 100, 100),
        bonus_deltas=(1_000, 1_000, 1_000),
    )
    assert service._sort_key(opportunity)[0] < service._sort_key(bonus)[0]


def test_qualified_precedes_conditional_when_all_other_sort_fields_tie() -> None:
    qualified = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("1.50", "2.00", "2.00"),
        stakes=(100, 100, 100),
        bonus_deltas=(1_000, 1_000, 1_000),
    )
    conditional = _opportunity(
        OpportunityClass.BONUS_CONDITIONAL,
        odds=("1.50", "2.00", "2.00"),
        stakes=(100, 100, 100),
        bonus_deltas=(1_000, 1_000, 1_000),
    )

    assert sorted((conditional, qualified), key=service._sort_key) == [qualified, conditional]


def test_bonus_sorting_uses_adjusted_minimum_not_standard_minimum() -> None:
    higher_adjusted = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("1.50", "2.00", "2.00"),
        stakes=(100, 100, 100),
        bonus_deltas=(1_000, 1_000, 1_000),
    )
    lower_adjusted = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("1.50", "2.00", "2.00"),
        stakes=(100, 100, 100),
        bonus_deltas=(900, 900, 900),
    )

    assert (
        higher_adjusted.minimum_standard_profit_cents
        == lower_adjusted.minimum_standard_profit_cents
    )
    assert (
        higher_adjusted.minimum_adjusted_profit_cents > lower_adjusted.minimum_adjusted_profit_cents
    )
    assert sorted((lower_adjusted, higher_adjusted), key=service._sort_key) == [
        higher_adjusted,
        lower_adjusted,
    ]


def test_final_allocation_tuple_breaks_tie_after_every_prior_field() -> None:
    first = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("4.00", "4.00", "4.00"),
        stakes=(100, 100, 200),
        bonus_deltas=(1, 1, 1),
    )
    second = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("4.00", "4.00", "4.00"),
        stakes=(100, 200, 100),
        bonus_deltas=(1, 1, 1),
    )
    first_key = service._sort_key(first)
    second_key = service._sort_key(second)

    assert first_key[:-1] == second_key[:-1]
    assert first_key[-1] == (100, 100, 200)
    assert second_key[-1] == (100, 200, 100)
    assert sorted((second, first), key=service._sort_key) == [first, second]


def test_fraction_comparison_preserves_close_exact_ratios_float_would_collapse() -> None:
    base = 10**18
    first = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("2.00", "2.00", "2.00"),
        stakes=(base, base, base),
        bonus_deltas=(base + 1, base + 1, base + 1),
    )
    second = _opportunity(
        OpportunityClass.BONUS_QUALIFIED,
        odds=("2.00", "2.00", "2.00"),
        stakes=(base + 1, base + 1, base + 1),
        bonus_deltas=(base + 2, base + 2, base + 2),
    )
    first_ratio = service._sort_key(first)[2]
    second_ratio = service._sort_key(second)[2]

    assert float(first_ratio) == float(second_ratio)
    assert first_ratio != second_ratio


def test_epoch_microsecond_ordering_does_not_use_float_timestamp() -> None:
    distant = datetime(9999, 1, 1, tzinfo=UTC)
    next_microsecond = distant + timedelta(microseconds=1)
    first = _opportunity(observed_at=(distant, distant, distant))
    second = _opportunity(observed_at=(next_microsecond, next_microsecond, next_microsecond))

    assert distant.timestamp() == next_microsecond.timestamp()
    assert service._sort_key(first)[8] != service._sort_key(second)[8]

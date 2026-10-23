from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import (
    Allocation,
    AnalysisRequest,
    BonusState,
    Opportunity,
    Quote,
    ScenarioResult,
    SourceAccount,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _account(*, bonus: BonusState | None = None) -> SourceAccount:
    return SourceAccount(balance_cents=10_000, max_stake_cents=10_000, bonus=bonus)


def _request(accounts: dict[Source, SourceAccount] | None = None) -> AnalysisRequest:
    return AnalysisRequest(
        budget_cents=300,
        now=NOW,
        accounts=accounts or {source: _account() for source in Source},
    )


def _quote(outcome: Outcome, *, event_id: str = "evt-1") -> Quote:
    odds = {Outcome.HOME: "2.0", Outcome.DRAW: "3.0", Outcome.AWAY: "4.0"}[outcome]
    return Quote(
        source=Source.SOURCE_B, event_id=event_id, outcome=outcome, odds=odds, observed_at=NOW
    )


def _valid_opportunity() -> Opportunity:
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    scenarios = (
        ScenarioResult(
            outcome=Outcome.HOME,
            gross_return_cents=200,
            standard_profit_cents=-100,
            bonus_delta_cents=0,
            adjusted_profit_cents=-100,
        ),
        ScenarioResult(
            outcome=Outcome.DRAW,
            gross_return_cents=300,
            standard_profit_cents=0,
            bonus_delta_cents=0,
            adjusted_profit_cents=0,
        ),
        ScenarioResult(
            outcome=Outcome.AWAY,
            gross_return_cents=400,
            standard_profit_cents=100,
            bonus_delta_cents=0,
            adjusted_profit_cents=100,
        ),
    )
    return Opportunity(
        classification="not_profitable",
        quotes={outcome: _quote(outcome) for outcome in Outcome},
        allocation=allocation,
        scenarios=scenarios,
        minimum_standard_profit_cents=-100,
        minimum_adjusted_profit_cents=-100,
    )


def test_quote_rejects_binary_float_odds() -> None:
    with pytest.raises(ValidationError):
        Quote(
            source=Source.SOURCE_B,
            event_id="evt-1",
            outcome=Outcome.HOME,
            odds=2.1,
            observed_at=datetime.now(UTC),
        )


def test_quote_accepts_four_decimal_string() -> None:
    quote = Quote(
        source=Source.SOURCE_B,
        event_id="evt-1",
        outcome=Outcome.HOME,
        odds="2.1234",
        observed_at=datetime.now(UTC),
    )
    assert quote.odds == Decimal("2.1234")


def test_analysis_request_rejects_source_a_bonus_but_allows_source_b_and_source_c() -> None:
    bonus = BonusState(loss_before_cents=100, net_deposit_cents=100, period_anchor=NOW)
    with pytest.raises(ValidationError):
        _request(
            {
                Source.SOURCE_A: _account(bonus=bonus),
                Source.SOURCE_B: _account(),
                Source.SOURCE_C: _account(),
            }
        )
    request = _request(
        {
            Source.SOURCE_A: _account(),
            Source.SOURCE_B: _account(bonus=bonus),
            Source.SOURCE_C: _account(bonus=bonus),
        }
    )
    assert request.accounts[Source.SOURCE_B].bonus == bonus
    assert request.accounts[Source.SOURCE_C].bonus == bonus


def test_domain_datetimes_accept_aware_iso_strings() -> None:
    quote = Quote(
        source=Source.SOURCE_B,
        event_id="evt",
        outcome=Outcome.HOME,
        odds="2.0",
        observed_at="2026-08-24T12:00:00Z",
    )
    assert quote.observed_at.tzinfo is not None


def test_analysis_request_bonus_evaluation_at_is_aware_and_not_before_now() -> None:
    request = _request()
    assert request.bonus_evaluation_at is None
    future = AnalysisRequest(
        budget_cents=300,
        now=NOW,
        bonus_evaluation_at=NOW + timedelta(hours=1),
        accounts=request.accounts,
    )
    assert future.bonus_evaluation_at is not None
    with pytest.raises(ValidationError):
        AnalysisRequest(
            budget_cents=300,
            now=NOW,
            bonus_evaluation_at=NOW.replace(tzinfo=None),
            accounts=request.accounts,
        )
    with pytest.raises(ValidationError):
        AnalysisRequest(
            budget_cents=300,
            now=NOW,
            bonus_evaluation_at=NOW - timedelta(seconds=1),
            accounts=request.accounts,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        NOW.replace(tzinfo=None),
        1_724_497_200,
        1.2,
        True,
        "not-a-date",
        NOW.isoformat(" "),
        NOW.isoformat("x"),
    ],
)
def test_domain_datetimes_reject_unaware_or_coerced_values(timestamp: object) -> None:
    with pytest.raises(ValidationError):
        Quote(
            source=Source.SOURCE_B,
            event_id="evt",
            outcome=Outcome.HOME,
            odds="2.0",
            observed_at=timestamp,
        )


def test_nested_domain_maps_are_defensive_and_immutable() -> None:
    accounts = {source: _account() for source in Source}
    request = _request(accounts)
    accounts[Source.SOURCE_C] = SourceAccount(balance_cents=1, max_stake_cents=10_000)
    assert request.accounts[Source.SOURCE_C].balance_cents == 10_000
    with pytest.raises(TypeError):
        request.accounts[Source.SOURCE_C] = _account()  # type: ignore[index]

    stakes = {outcome: 100 for outcome in Outcome}
    allocation = Allocation(stakes_cents=stakes)
    stakes[Outcome.HOME] = 1
    assert allocation.stakes_cents[Outcome.HOME] == 100
    with pytest.raises(TypeError):
        allocation.stakes_cents[Outcome.HOME] = 1  # type: ignore[index]

    opportunity = _valid_opportunity()
    quotes = opportunity.quotes
    with pytest.raises(TypeError):
        quotes[Outcome.HOME] = _quote(Outcome.HOME)  # type: ignore[index]
    assert '"quotes"' in opportunity.model_dump_json()


def test_quote_supports_handicap_line() -> None:
    from surebet.domain.enums import MarketType, Outcome, QuoteStatus, Source
    from surebet.domain.models import Quote

    q = Quote(
        source=Source.SOURCE_A,
        event_id="evt-bask-1",
        outcome=Outcome.HOME,
        odds=Decimal("1.95"),
        observed_at=NOW,
        status=QuoteStatus.OPEN,
        market_type=MarketType.BASKETBALL_ASIAN_HANDICAP,
        line=Decimal("-4.5"),
    )
    assert q.line == Decimal("-4.5")
    assert q.market_type is MarketType.BASKETBALL_ASIAN_HANDICAP


def test_mapping_boundaries_round_trip_through_dump_and_deep_copy() -> None:
    opportunity = _valid_opportunity()
    copied = opportunity.model_copy(deep=True)
    assert copied.model_dump() == opportunity.model_dump()
    assert (
        Opportunity.model_validate(opportunity.model_dump()).model_dump()
        == opportunity.model_dump()
    )
    with pytest.raises((TypeError, AttributeError)):
        dict.__setitem__(opportunity.quotes, Outcome.HOME, _quote(Outcome.HOME))  # type: ignore[arg-type]
    with pytest.raises((TypeError, AttributeError)):
        dict.__init__(opportunity.quotes, {Outcome.HOME: _quote(Outcome.HOME)})  # type: ignore[arg-type]
    with pytest.raises((TypeError, AttributeError)):
        opportunity.quotes.update({Outcome.HOME: _quote(Outcome.HOME)})  # type: ignore[attr-defined]
    with pytest.raises((TypeError, AttributeError)):
        opportunity.quotes._data = {}  # type: ignore[attr-defined]


def test_valid_opportunity_construction_preserves_consistent_scenarios() -> None:
    opportunity = _valid_opportunity()
    assert len(opportunity.scenarios) == 3
    assert opportunity.minimum_standard_profit_cents == -100
    assert opportunity.minimum_adjusted_profit_cents == -100


def test_opportunity_rejects_quote_key_outcome_event_and_scenario_contradictions() -> None:
    valid = _valid_opportunity()
    bad_quotes = dict(valid.quotes)
    bad_quotes[Outcome.HOME] = _quote(Outcome.DRAW)
    with pytest.raises(ValidationError):
        Opportunity(
            classification=valid.classification,
            quotes=bad_quotes,
            allocation=valid.allocation,
            scenarios=valid.scenarios,
            minimum_standard_profit_cents=-100,
            minimum_adjusted_profit_cents=-100,
        )

    bad_event_quotes = {outcome: _quote(outcome, event_id=outcome.value) for outcome in Outcome}
    with pytest.raises(ValidationError):
        Opportunity(
            classification=valid.classification,
            quotes=bad_event_quotes,
            allocation=valid.allocation,
            scenarios=valid.scenarios,
            minimum_standard_profit_cents=-100,
            minimum_adjusted_profit_cents=-100,
        )

    with pytest.raises(ValidationError):
        Opportunity(
            classification=valid.classification,
            quotes=valid.quotes,
            allocation=valid.allocation,
            scenarios=(valid.scenarios[0], valid.scenarios[0], valid.scenarios[2]),
            minimum_standard_profit_cents=-100,
            minimum_adjusted_profit_cents=-100,
        )


@pytest.mark.parametrize(
    "field, value",
    [
        ("minimum_standard_profit_cents", -99),
        ("minimum_adjusted_profit_cents", -99),
        (
            "scenarios",
            (
                ScenarioResult(
                    outcome=Outcome.HOME,
                    gross_return_cents=200,
                    standard_profit_cents=0,
                    bonus_delta_cents=0,
                    adjusted_profit_cents=0,
                ),
            ),
        ),
    ],
)
def test_opportunity_rejects_declared_or_calculated_values_that_disagree(
    field: str, value: object
) -> None:
    valid = _valid_opportunity()
    changes = {field: value}
    with pytest.raises(ValidationError):
        Opportunity(**{**valid.model_dump(), **changes})


@pytest.mark.parametrize(
    "scenario_field, value",
    [("gross_return_cents", 201), ("standard_profit_cents", -99), ("adjusted_profit_cents", -99)],
)
def test_opportunity_rejects_inconsistent_scenario_math(scenario_field: str, value: int) -> None:
    valid = _valid_opportunity()
    original = valid.scenarios[0]
    changed = original.model_copy(update={scenario_field: value})
    with pytest.raises(ValidationError):
        Opportunity(
            classification=valid.classification,
            quotes=valid.quotes,
            allocation=valid.allocation,
            scenarios=(changed, *valid.scenarios[1:]),
            minimum_standard_profit_cents=-100,
            minimum_adjusted_profit_cents=-100,
        )


def _classified_opportunity(
    classification: str, minimum_standard: int, minimum_adjusted: int
) -> Opportunity:
    odds = "4.0" if minimum_standard > 0 else "2.0"
    gross = 400 if minimum_standard > 0 else 200
    bonus_delta = minimum_adjusted - minimum_standard
    allocation = Allocation(stakes_cents={outcome: 100 for outcome in Outcome})
    quotes = {
        outcome: Quote(
            source=Source.SOURCE_B, event_id="evt-1", outcome=outcome, odds=odds, observed_at=NOW
        )
        for outcome in Outcome
    }
    scenarios = tuple(
        ScenarioResult(
            outcome=outcome,
            gross_return_cents=gross,
            standard_profit_cents=minimum_standard,
            bonus_delta_cents=bonus_delta,
            adjusted_profit_cents=minimum_adjusted,
        )
        for outcome in Outcome
    )
    return Opportunity(
        classification=classification,
        quotes=quotes,
        allocation=allocation,
        scenarios=scenarios,
        minimum_standard_profit_cents=minimum_standard,
        minimum_adjusted_profit_cents=minimum_adjusted,
    )


@pytest.mark.parametrize(
    ("classification", "minimum_standard", "minimum_adjusted"),
    [
        ("standard_arbitrage", 100, 100),
        ("bonus_qualified", -100, 1),
        ("bonus_conditional", -100, 1),
        ("not_profitable", -100, -100),
    ],
)
def test_opportunity_accepts_classification_matching_numeric_invariants(
    classification: str, minimum_standard: int, minimum_adjusted: int
) -> None:
    assert (
        _classified_opportunity(
            classification, minimum_standard, minimum_adjusted
        ).classification.value
        == classification
    )


@pytest.mark.parametrize(
    ("classification", "minimum_standard", "minimum_adjusted"),
    [
        ("standard_arbitrage", -100, 1),
        ("bonus_qualified", 100, 100),
        ("bonus_conditional", -100, -100),
        ("not_profitable", 100, 100),
    ],
)
def test_opportunity_rejects_classification_contradicting_numeric_invariants(
    classification: str, minimum_standard: int, minimum_adjusted: int
) -> None:
    with pytest.raises(ValidationError):
        _classified_opportunity(classification, minimum_standard, minimum_adjusted)

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from surebet.arbitrage.math import equal_return_seed
from surebet.domain.enums import OpportunityClass, Outcome, Source
from surebet.domain.models import AnalysisRequest, Quote, SourceAccount
from surebet.money import odds_scaled, payout_cents
from surebet.opportunities.service import find_opportunities

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
ODDS_TEXT = st.sampled_from(("2.9000", "3.1000", "3.6000", "4.0000"))
EQUIVALENT_ODDS = (
    st.sampled_from(("2.90", "2.900", "2.9000")),
    st.sampled_from(("3.60", "3.600", "3.6000")),
    st.sampled_from(("3.10", "3.100", "3.1000")),
)
MONEY = st.integers(min_value=0, max_value=20_000)


@settings(max_examples=25, derandomize=True, deadline=None)
@given(st.integers(min_value=0, max_value=100_000), MONEY, ODDS_TEXT)
def test_conservative_payout_is_exact_scaled_integer_math(
    stake_cents: int, _unused: int, odds_text: str
) -> None:
    odds = Decimal(odds_text)
    assert payout_cents(stake_cents, odds) == stake_cents * odds_scaled(odds) // 10_000


@settings(max_examples=25, derandomize=True, deadline=None)
@given(
    st.permutations(tuple(Outcome)),
    st.integers(min_value=0, max_value=20_000),
)
def test_equal_return_seed_is_complete_and_mapping_order_independent(
    order: tuple[Outcome, ...], budget: int
) -> None:
    base = {
        outcome: Decimal(value)
        for outcome, value in zip(Outcome, ("2.90", "3.60", "3.10"), strict=True)
    }
    odds = {outcome: base[outcome] for outcome in order}
    stakes = equal_return_seed(odds, budget)
    reversed_stakes = equal_return_seed(dict(reversed(tuple(odds.items()))), budget)
    assert tuple(stakes) == tuple(Outcome)
    assert set(stakes) == set(Outcome)
    assert all(isinstance(value, int) and value >= 0 for value in stakes.values())
    assert sum(stakes.values()) == budget
    assert stakes == reversed_stakes


def _request(scale: int) -> AnalysisRequest:
    caps = (360 * scale, 290 * scale, 337 * scale)
    payouts = (1044 * scale, 1044 * scale, {1: 1044, 2: 2089, 3: 3131}[scale])
    accounts = {
        source: SourceAccount(
            balance_cents=cap,
            max_stake_cents=cap,
            max_payout_cents=payout,
        )
        for source, cap, payout in zip(
            (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C), caps, payouts, strict=True
        )
    }
    # Sources the generated quotes never use still need an account: the invariant
    # assertions below range over the whole `Source` enum.
    for unused_source in set(Source) - set(accounts):
        accounts[unused_source] = SourceAccount(balance_cents=100_000, max_stake_cents=100_000)
    return AnalysisRequest(
        budget_cents=1200 * scale,
        now=NOW,
        stale_after_seconds=75,
        accounts=accounts,
    )


@st.composite
def profitable_inputs(draw: st.DrawFn) -> tuple[tuple[Quote, ...], AnalysisRequest]:
    # All generated assignments use one known arbitrage shape; only the
    # representational Decimal scale and account order vary.
    scales = tuple(draw(strategy) for strategy in EQUIVALENT_ODDS)
    values = tuple(Decimal(value) for value in scales)
    # Keep the generated cases in the proven arbitrage region while varying
    # equivalent scales and small budgets around the solver's integer domain.
    values = (
        max(values[0], Decimal("2.90")),
        max(values[1], Decimal("3.60")),
        max(values[2], Decimal("3.10")),
    )
    scale = draw(st.integers(min_value=1, max_value=3))
    quotes = tuple(
        Quote(
            source=source,
            event_id="generated-event",
            outcome=outcome,
            odds=value,
            observed_at=NOW - timedelta(seconds=draw(st.integers(min_value=0, max_value=75))),
        )
        for outcome, source, value in zip(
            Outcome, (Source.SOURCE_A, Source.SOURCE_B, Source.SOURCE_C), values, strict=True
        )
    )
    return quotes, _request(scale)


@settings(max_examples=20, derandomize=True, deadline=None)
@given(profitable_inputs())
def test_generated_single_assignment_opportunities_are_verified(
    generated: tuple[tuple[Quote, ...], AnalysisRequest],
) -> None:
    quotes, request = generated
    first = find_opportunities(quotes, request)
    second = find_opportunities(tuple(reversed(quotes)), request)
    reordered_request = AnalysisRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "accounts": dict(
                reversed(tuple(request.model_dump(mode="python")["accounts"].items()))
            ),
        }
    )
    third = find_opportunities(quotes, reordered_request)
    assert first == second
    assert first == third
    assert first
    for opportunity in first:
        total = sum(opportunity.allocation.stakes_cents.values())
        assert 0 <= total <= request.budget_cents
        assert tuple(item.outcome for item in opportunity.scenarios) == tuple(Outcome)
        scenarios = {item.outcome: item for item in opportunity.scenarios}
        aggregate = {source: 0 for source in Source}
        for outcome in Outcome:
            stake = opportunity.allocation.stakes_cents[outcome]
            quote = opportunity.quotes[outcome]
            account = request.accounts[quote.source]
            assert account.min_stake_cents <= stake <= account.max_stake_cents
            assert stake <= account.max_stake_cents
            aggregate[quote.source] += stake
            assert scenarios[outcome].gross_return_cents == payout_cents(stake, quote.odds)
            assert account.max_payout_cents is not None
            assert scenarios[outcome].gross_return_cents <= account.max_payout_cents
            assert scenarios[outcome].standard_profit_cents == (
                scenarios[outcome].gross_return_cents - total
            )
            assert scenarios[outcome].adjusted_profit_cents == (
                scenarios[outcome].standard_profit_cents + scenarios[outcome].bonus_delta_cents
            )
            if account.max_payout_cents is not None:
                assert scenarios[outcome].gross_return_cents <= account.max_payout_cents
        assert all(aggregate[source] <= request.accounts[source].balance_cents for source in Source)
        assert any(
            opportunity.allocation.stakes_cents[outcome]
            == request.accounts[opportunity.quotes[outcome].source].max_stake_cents
            for outcome in Outcome
        )
        assert any(aggregate[source] == request.accounts[source].balance_cents for source in Source)
        assert any(
            scenarios[outcome].gross_return_cents
            == request.accounts[opportunity.quotes[outcome].source].max_payout_cents
            for outcome in Outcome
        )
        lowered_data = request.model_dump(mode="python")
        lowered_accounts = dict(request.accounts)
        selected_source = opportunity.quotes[Outcome.HOME].source
        selected_account = request.accounts[selected_source]
        assert selected_account.max_payout_cents is not None
        lowered_account = selected_account.model_copy(
            update={"max_payout_cents": selected_account.max_payout_cents - 1}
        )
        lowered_accounts[selected_source] = lowered_account
        lowered_data["accounts"] = lowered_accounts
        lowered_request = AnalysisRequest.model_validate(lowered_data)
        lowered = find_opportunities(quotes, lowered_request)
        assert lowered
        assert lowered[0].allocation != opportunity.allocation
        assert opportunity.minimum_standard_profit_cents == min(
            item.standard_profit_cents for item in opportunity.scenarios
        )
        assert opportunity.minimum_adjusted_profit_cents == min(
            item.adjusted_profit_cents for item in opportunity.scenarios
        )
        assert opportunity.classification is OpportunityClass.STANDARD
        assert opportunity.minimum_standard_profit_cents > 0


@settings(max_examples=20, derandomize=True, deadline=None)
@given(profitable_inputs())
def test_repeated_analysis_does_not_mutate_domain_input(
    generated: tuple[tuple[Quote, ...], AnalysisRequest],
) -> None:
    quotes, request = generated
    before_quotes = deepcopy(tuple(quote.model_dump(mode="python") for quote in quotes))
    before_request = deepcopy(request.model_dump(mode="python"))
    assert find_opportunities(quotes, request) == find_opportunities(quotes, request)
    assert tuple(quote.model_dump(mode="python") for quote in quotes) == before_quotes
    assert request.model_dump(mode="python") == before_request


def test_equal_return_seed_reference_uses_exact_fractions() -> None:
    odds = {outcome: Decimal("3.00") for outcome in Outcome}
    stakes = equal_return_seed(odds, 10001)
    assert stakes == {Outcome.HOME: 3334, Outcome.DRAW: 3334, Outcome.AWAY: 3333}
    assert Fraction(sum(stakes.values()), 1) == Fraction(10001, 1)

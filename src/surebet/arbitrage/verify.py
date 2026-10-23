"""Exact integer-kuruş verification of standard arbitrage allocations."""

from collections.abc import Mapping

from surebet.domain.enums import Outcome
from surebet.domain.models import Allocation, Quote, ScenarioResult
from surebet.money import payout_cents


def _validated_quotes(quotes: Mapping[Outcome, Quote]) -> tuple[Quote, ...]:
    if not isinstance(quotes, Mapping):
        raise TypeError("quotes must be a Mapping")
    if set(quotes) != set(Outcome):
        raise ValueError("quotes must contain exactly HOME, DRAW, and AWAY")

    values: list[Quote] = []
    for outcome in Outcome:
        quote = quotes[outcome]
        if not isinstance(quote, Quote):
            raise TypeError(f"quote for {outcome} must be a Quote")
        if quote.outcome is not outcome:
            raise ValueError("quote keys must match Quote.outcome")
        values.append(quote)
    if len({quote.event_id for quote in values}) != 1:
        raise ValueError("all quotes must belong to the same event")
    return tuple(values)


def verify_allocation(
    quotes: Mapping[Outcome, Quote], allocation: Allocation
) -> tuple[ScenarioResult, ...]:
    """Calculate conservative gross payout and standard profit per outcome."""
    validated_quotes = _validated_quotes(quotes)
    if not isinstance(allocation, Allocation):
        raise TypeError("allocation must be an Allocation")

    total_stake = sum(allocation.stakes_cents.values())
    scenarios: list[ScenarioResult] = []
    # `_validated_quotes` returns quotes in `Outcome` order, so zipping keeps the 1X2 type.
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        gross_return = payout_cents(allocation.stakes_cents[outcome], quote.odds)
        standard_profit = gross_return - total_stake
        scenarios.append(
            ScenarioResult(
                outcome=outcome,
                gross_return_cents=gross_return,
                standard_profit_cents=standard_profit,
                bonus_delta_cents=0,
                adjusted_profit_cents=standard_profit,
            )
        )
    return tuple(scenarios)

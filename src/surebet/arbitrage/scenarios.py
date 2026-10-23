"""Exact post-settlement bonus scenario evaluation."""

from collections.abc import Mapping
from datetime import UTC, datetime

from surebet.bonus.engine import bonus_delta
from surebet.domain.enums import Outcome, QuoteStatus, Source
from surebet.domain.models import Allocation, AnalysisRequest, Quote, ScenarioResult
from surebet.money import payout_cents


def _validated_quotes(
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


def _evaluation_instant(request: AnalysisRequest) -> datetime | None:
    instant = request.bonus_evaluation_at
    if instant is None:
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("bonus_evaluation_at must be timezone-aware")
    if instant.astimezone(UTC) < request.now.astimezone(UTC):
        raise ValueError("bonus_evaluation_at must not be earlier than now")
    return instant


def evaluate_scenarios(
    quotes: Mapping[Outcome, Quote],
    allocation: Allocation,
    request: AnalysisRequest,
    *,
    assume_confirmed: bool = False,
) -> tuple[ScenarioResult, ...]:
    """Evaluate every winning outcome with independent per-source bonus state."""
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest")
    if not isinstance(allocation, Allocation):
        raise TypeError("allocation must be an Allocation")
    if type(assume_confirmed) is not bool:
        raise TypeError("assume_confirmed must be a bool")
    validated_quotes = _validated_quotes(quotes, request)
    instant = _evaluation_instant(request)
    total_stake = sum(allocation.stakes_cents.values())
    if instant is None:
        # `_validated_quotes` returns quotes in `Outcome` order, so zipping keeps the 1X2 type.
        return tuple(
            ScenarioResult(
                outcome=outcome,
                gross_return_cents=payout_cents(allocation.stakes_cents[outcome], quote.odds),
                standard_profit_cents=(
                    payout_cents(allocation.stakes_cents[outcome], quote.odds) - total_stake
                ),
                bonus_delta_cents=0,
                adjusted_profit_cents=(
                    payout_cents(allocation.stakes_cents[outcome], quote.odds) - total_stake
                ),
            )
            for outcome, quote in zip(Outcome, validated_quotes, strict=True)
        )

    assigned_sources = tuple(
        source for source in Source if source in {quote.source for quote in validated_quotes}
    )
    for source in assigned_sources:
        state = request.accounts[source].bonus
        if state is not None and instant.astimezone(UTC) < state.period_anchor.astimezone(UTC):
            raise ValueError(
                "bonus_evaluation_at must not be before an assigned bonus period_anchor"
            )

    source_stakes = {source: 0 for source in Source}
    for outcome, quote in zip(Outcome, validated_quotes, strict=True):
        source_stakes[quote.source] += allocation.stakes_cents[outcome]

    scenarios: list[ScenarioResult] = []
    for winning_outcome, winning_quote in zip(Outcome, validated_quotes, strict=True):
        gross_total = payout_cents(allocation.stakes_cents[winning_outcome], winning_quote.odds)
        standard_profit = gross_total - total_stake
        total_bonus_delta = 0
        for source in assigned_sources:
            state = request.accounts[source].bonus
            if state is None:
                continue
            site_stakes = source_stakes[source]
            site_gross = gross_total if winning_quote.source is source else 0
            site_pnl = site_gross - site_stakes
            loss_after = max(0, state.loss_before_cents - site_pnl)
            balance_after = request.accounts[source].balance_cents - site_stakes + site_gross
            total_bonus_delta += bonus_delta(
                state,
                before_balance_cents=request.accounts[source].balance_cents,
                after_balance_cents=balance_after,
                after_loss_cents=loss_after,
                now=instant,
                assume_confirmed=assume_confirmed,
            )
        scenarios.append(
            ScenarioResult(
                outcome=winning_outcome,
                gross_return_cents=gross_total,
                standard_profit_cents=standard_profit,
                bonus_delta_cents=total_bonus_delta,
                adjusted_profit_cents=standard_profit + total_bonus_delta,
            )
        )
    return tuple(scenarios)

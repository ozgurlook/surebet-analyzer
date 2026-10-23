"""Resolve quotes and enumerate deterministic verified opportunities."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from itertools import product

from surebet.arbitrage.scenarios import evaluate_scenarios
from surebet.arbitrage.solver import optimize_adjusted, optimize_standard
from surebet.domain.enums import OpportunityClass, Outcome, QuoteStatus, Source, TwoWayOutcome
from surebet.domain.models import Allocation, AnalysisRequest, Opportunity, Quote, ScenarioResult
from surebet.money import odds_scaled

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SOURCE_INDEX = {source: index for index, source in enumerate(Source)}
_CLASS_RANK = {
    OpportunityClass.STANDARD: 0,
    OpportunityClass.BONUS_QUALIFIED: 1,
    OpportunityClass.BONUS_CONDITIONAL: 2,
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _epoch_microseconds(value: datetime) -> int:
    elapsed = _utc(value) - _EPOCH
    return elapsed.days * 86_400 * 1_000_000 + elapsed.seconds * 1_000_000 + elapsed.microseconds


def _canonical_odds(value: Decimal) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise TypeError("odds must have a finite numeric exponent")
    normalized_digits = list(digits)
    while len(normalized_digits) > 1 and normalized_digits[-1] == 0:
        normalized_digits.pop()
        exponent += 1
    return Decimal((sign, tuple(normalized_digits), exponent))


def _resolve_snapshots(
    quotes: Sequence[Quote],
) -> dict[tuple[Source, Outcome | TwoWayOutcome], Quote | None]:
    grouped: dict[tuple[Source, Outcome | TwoWayOutcome], list[Quote]] = {}
    for quote in quotes:
        key = (quote.source, quote.outcome)
        grouped.setdefault(key, []).append(quote)

    resolved: dict[tuple[Source, Outcome | TwoWayOutcome], Quote | None] = {}
    for key, observations in grouped.items():
        latest_instant = max(_utc(quote.observed_at) for quote in observations)
        latest = [quote for quote in observations if _utc(quote.observed_at) == latest_instant]
        first = latest[0]
        if any(
            quote.status is not first.status or quote.odds != first.odds for quote in latest[1:]
        ):
            resolved[key] = None
            continue
        resolved[key] = Quote(
            source=first.source,
            event_id=first.event_id,
            outcome=first.outcome,
            odds=_canonical_odds(first.odds),
            observed_at=latest_instant,
            status=first.status,
        )
    return resolved


def _usable_snapshots(
    resolved: Mapping[tuple[Source, Outcome | TwoWayOutcome], Quote | None],
    request: AnalysisRequest,
) -> dict[Outcome, tuple[Quote, ...]]:
    now = _utc(request.now)
    stale_after = timedelta(seconds=request.stale_after_seconds)
    by_outcome: dict[Outcome, list[Quote]] = {outcome: [] for outcome in Outcome}
    for quote in resolved.values():
        if quote is None or quote.status is not QuoteStatus.OPEN:
            continue
        if not isinstance(quote.outcome, Outcome):
            continue
        age = now - _utc(quote.observed_at)
        if age < timedelta(0) or age > stale_after:
            continue
        by_outcome[quote.outcome].append(quote)
    return {
        outcome: tuple(sorted(values, key=lambda quote: _SOURCE_INDEX[quote.source]))
        for outcome, values in by_outcome.items()
    }


def _opportunity(
    classification: OpportunityClass,
    quotes: Mapping[Outcome, Quote],
    allocation: Allocation,
    scenarios: tuple[ScenarioResult, ...],
) -> Opportunity:
    minimum_standard = min(scenario.standard_profit_cents for scenario in scenarios)
    minimum_adjusted = min(scenario.adjusted_profit_cents for scenario in scenarios)
    return Opportunity(
        classification=classification,
        quotes=quotes,
        allocation=allocation,
        scenarios=scenarios,
        minimum_standard_profit_cents=minimum_standard,
        minimum_adjusted_profit_cents=minimum_adjusted,
    )


def _sort_key(opportunity: Opportunity) -> tuple[object, ...]:
    classification = opportunity.classification
    relevant_minimum = (
        opportunity.minimum_standard_profit_cents
        if classification is OpportunityClass.STANDARD
        else opportunity.minimum_adjusted_profit_cents
    )
    selected_quotes = tuple(opportunity.quotes[outcome] for outcome in Outcome)
    total_stake = sum(opportunity.allocation.stakes_cents.values())
    oldest = min(_epoch_microseconds(quote.observed_at) for quote in selected_quotes)
    return (
        _CLASS_RANK[classification],
        -relevant_minimum,
        -Fraction(relevant_minimum, total_stake),
        -oldest,
        total_stake,
        selected_quotes[0].event_id,
        tuple(_SOURCE_INDEX[quote.source] for quote in selected_quotes),
        tuple(-odds_scaled(quote.odds) for quote in selected_quotes),
        tuple(-_epoch_microseconds(quote.observed_at) for quote in selected_quotes),
        tuple(opportunity.allocation.stakes_cents[outcome] for outcome in Outcome),
    )


def _classify_assignment(
    quotes: Mapping[Outcome, Quote], request: AnalysisRequest
) -> Opportunity | None:
    standard_allocation = optimize_standard(quotes, request)
    if standard_allocation is not None:
        standard_scenarios = evaluate_scenarios(
            quotes, standard_allocation, request, assume_confirmed=False
        )
        if min(item.standard_profit_cents for item in standard_scenarios) > 0:
            return _opportunity(
                OpportunityClass.STANDARD,
                quotes,
                standard_allocation,
                standard_scenarios,
            )

    strict_allocation = optimize_adjusted(quotes, request, assume_confirmed=False)
    if strict_allocation is not None:
        strict_scenarios = evaluate_scenarios(
            quotes, strict_allocation, request, assume_confirmed=False
        )
        if (
            min(item.standard_profit_cents for item in strict_scenarios) <= 0
            and min(item.adjusted_profit_cents for item in strict_scenarios) > 0
        ):
            return _opportunity(
                OpportunityClass.BONUS_QUALIFIED,
                quotes,
                strict_allocation,
                strict_scenarios,
            )

    assumed_allocation = optimize_adjusted(quotes, request, assume_confirmed=True)
    if assumed_allocation is None:
        return None
    assumed_scenarios = evaluate_scenarios(
        quotes, assumed_allocation, request, assume_confirmed=True
    )
    if (
        min(item.standard_profit_cents for item in assumed_scenarios) <= 0
        and min(item.adjusted_profit_cents for item in assumed_scenarios) > 0
    ):
        return _opportunity(
            OpportunityClass.BONUS_CONDITIONAL,
            quotes,
            assumed_allocation,
            assumed_scenarios,
        )
    return None


def find_opportunities(
    quotes: Sequence[Quote], request: AnalysisRequest
) -> tuple[Opportunity, ...]:
    """Return all verified profitable opportunities for one event."""
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be an AnalysisRequest")
    if not isinstance(quotes, Sequence) or isinstance(quotes, (str, bytes, bytearray)):
        raise TypeError("quotes must be a sequence")
    if not quotes:
        return ()
    if any(not isinstance(quote, Quote) for quote in quotes):
        raise TypeError("quotes must contain only Quote objects")
    event_ids = {quote.event_id for quote in quotes}
    if len(event_ids) != 1:
        raise ValueError("all quotes must belong to exactly one event")

    usable = _usable_snapshots(_resolve_snapshots(quotes), request)
    if any(not usable[outcome] for outcome in Outcome):
        return ()

    opportunities: list[Opportunity] = []
    candidates = [usable[outcome] for outcome in Outcome]
    for selected in product(*candidates):
        assignment = {outcome: quote for outcome, quote in zip(Outcome, selected, strict=True)}
        opportunity = _classify_assignment(assignment, request)
        if opportunity is not None:
            opportunities.append(opportunity)
    return tuple(sorted(opportunities, key=_sort_key))

"""Acceptance coverage for source-neutral analysis of public source batches."""

from datetime import UTC, datetime
from decimal import Decimal

from surebet.domain.enums import OpportunityClass, Outcome, Source
from surebet.domain.models import AnalysisRequest, BonusState, Quote, SourceAccount
from surebet.opportunities.service import find_opportunities
from surebet.sources.models import FULL_TIME_RESULT_MARKET, CollectedEvent, CollectionBatch

OBSERVED_AT = datetime(2026, 8, 24, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 25, 19, tzinfo=UTC)


def test_combined_public_batches_preserve_standard_and_bonus_classifications() -> None:
    """Dropping an adapter batch would change the established opportunity classes."""

    classifications = {
        name: tuple(
            opportunity.classification
            for opportunity in find_opportunities(
                tuple(
                    quote
                    for batch in batches
                    for event in batch.events
                    for quote in event.quotes.values()
                ),
                request,
            )
        )
        for name, batches, request in combined_fixture_batches()
    }

    assert classifications == {
        "standard": (OpportunityClass.STANDARD,),
        "bonus_qualified": (
            OpportunityClass.BONUS_QUALIFIED,
            OpportunityClass.BONUS_QUALIFIED,
        ),
        "bonus_conditional": (
            OpportunityClass.BONUS_CONDITIONAL,
            OpportunityClass.BONUS_CONDITIONAL,
        ),
    }


def combined_fixture_batches() -> tuple[
    tuple[str, tuple[CollectionBatch, ...], AnalysisRequest], ...
]:
    """Return matched, sanitized batches in the unchanged analysis input shape."""

    return (
        (
            "standard",
            _batches("standard", ("2.90", "3.60", "3.10")),
            _request(budget_cents=10_000),
        ),
        (
            "bonus_qualified",
            _batches("bonus-qualified", ("1.50", "2.00", "2.00")),
            _request(budget_cents=300, source_b_bonus_confirmed=True),
        ),
        (
            "bonus_conditional",
            _batches("bonus-conditional", ("1.50", "2.00", "2.00")),
            _request(budget_cents=300, source_b_bonus_confirmed=False),
        ),
    )


def _batches(event_id: str, prices: tuple[str, str, str]) -> tuple[CollectionBatch, ...]:
    sources = (Source.SOURCE_C, Source.SOURCE_B, Source.SOURCE_A)
    selected = dict(zip(Outcome, zip(sources, prices, strict=True), strict=True))
    return tuple(
        CollectionBatch(
            source=source,
            started_at=OBSERVED_AT,
            finished_at=OBSERVED_AT,
            events=(
                CollectedEvent(
                    source=source,
                    source_event_id=event_id,
                    competition="Fixture League",
                    home="Fixture Home",
                    away="Fixture Away",
                    kickoff=KICKOFF,
                    market_key=FULL_TIME_RESULT_MARKET,
                    quotes={
                        outcome: Quote(
                            source=source,
                            event_id=event_id,
                            outcome=outcome,
                            odds=price if source is selected[outcome][0] else Decimal("1.01"),
                            observed_at=OBSERVED_AT,
                        )
                        for outcome, price in zip(Outcome, prices, strict=True)
                    },
                ),
            ),
        )
        for source in sources
    )


def _request(*, budget_cents: int, source_b_bonus_confirmed: bool | None = None) -> AnalysisRequest:
    source_b_bonus = (
        None
        if source_b_bonus_confirmed is None
        else BonusState(
            loss_before_cents=9_950,
            net_deposit_cents=10_000,
            period_anchor=OBSERVED_AT,
            requirements_confirmed=source_b_bonus_confirmed,
        )
    )
    return AnalysisRequest(
        budget_cents=budget_cents,
        now=OBSERVED_AT,
        bonus_evaluation_at=(OBSERVED_AT.replace(hour=13) if source_b_bonus is not None else None),
        accounts={
            Source.SOURCE_A: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
            Source.SOURCE_B: SourceAccount(
                balance_cents=200 if source_b_bonus is not None else 100_000,
                max_stake_cents=100_000,
                bonus=source_b_bonus,
            ),
            Source.SOURCE_C: SourceAccount(balance_cents=100_000, max_stake_cents=100_000),
        },
    )

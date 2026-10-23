from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from surebet.domain.enums import Outcome, Source
from surebet.domain.models import Quote
from surebet.sources.models import CollectedEvent, CollectionBatch
from surebet.taxonomy.models import SourceCompetitionEvidence, SourceIdentityKind

OBSERVED_AT = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 25, 19, 0, tzinfo=UTC)


def quote(outcome: Outcome, odds: str) -> Quote:
    return Quote(
        source=Source.SOURCE_A,
        event_id="30549576",
        outcome=outcome,
        odds=odds,
        observed_at=OBSERVED_AT,
    )


def collected_event() -> CollectedEvent:
    return CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="30549576",
        competition="UEFA Europa League",
        home="Bodo/Glimt",
        away="NEC Nijmegen",
        kickoff=KICKOFF,
        market_key="football.full_time_result.90m",
        quotes={
            Outcome.HOME: quote(Outcome.HOME, "1.4700"),
            Outcome.DRAW: quote(Outcome.DRAW, "4.3300"),
            Outcome.AWAY: quote(Outcome.AWAY, "4.6000"),
        },
    )


def test_collected_event_preserves_exact_prices_and_defensively_freezes_quotes() -> None:
    mutable_quotes = dict(collected_event().quotes)
    event = CollectedEvent(
        source=Source.SOURCE_A,
        source_event_id="30549576",
        competition="UEFA Europa League",
        home="Bodo/Glimt",
        away="NEC Nijmegen",
        kickoff=KICKOFF,
        market_key="football.full_time_result.90m",
        quotes=mutable_quotes,
    )

    mutable_quotes[Outcome.HOME] = quote(Outcome.HOME, "9.9900")

    assert event.quotes[Outcome.HOME].odds == Decimal("1.4700")
    with pytest.raises(TypeError):
        event.quotes[Outcome.HOME] = quote(Outcome.HOME, "2.0000")  # type: ignore[index]


def test_old_snapshot_event_without_taxonomy_still_deserializes() -> None:
    event_payload = collected_event().model_dump()
    event_payload.pop("source_competition", None)

    event = CollectedEvent.model_validate(event_payload)

    assert event.source_competition is None


def test_source_competition_evidence_is_immutable() -> None:
    evidence = SourceCompetitionEvidence(
        provider="source_a",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        country_id="20",
        competition_id="18286520",
        competition_name="UEFA Avrupa Ligi",
    )

    with pytest.raises(ValidationError):
        evidence.competition_name = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["provider", "competition_id", "competition_name"])
def test_source_competition_evidence_rejects_blank_identity_fields(field: str) -> None:
    evidence_payload = SourceCompetitionEvidence(
        provider="source_a",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="18286520",
        competition_name="UEFA Avrupa Ligi",
    ).model_dump()
    evidence_payload[field] = " "

    with pytest.raises(ValidationError, match=f"{field} must be nonempty"):
        SourceCompetitionEvidence.model_validate(evidence_payload)


def test_collected_event_rejects_source_competition_from_another_provider() -> None:
    event_payload = collected_event().model_dump()
    event_payload["source_competition"] = SourceCompetitionEvidence(
        provider="source_b",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="18286520",
        competition_name="UEFA Avrupa Ligi",
    ).model_dump()

    with pytest.raises(
        ValidationError, match="source competition provider must match event source"
    ):
        CollectedEvent.model_validate(event_payload)


def test_collected_event_rejects_incomplete_1x2_quotes() -> None:
    with pytest.raises(ValidationError, match="exactly HOME, DRAW, and AWAY"):
        CollectedEvent(
            source=Source.SOURCE_A,
            source_event_id="30549576",
            competition="UEFA Europa League",
            home="Bodo/Glimt",
            away="NEC Nijmegen",
            kickoff=KICKOFF,
            market_key="football.full_time_result.90m",
            quotes={
                Outcome.HOME: quote(Outcome.HOME, "1.4700"),
                Outcome.AWAY: quote(Outcome.AWAY, "4.6000"),
            },
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_event_id", "   ", "source_event_id must be nonempty"),
        ("competition", "", "competition must be nonempty"),
        ("home", " ", "home must be nonempty"),
        ("away", "", "away must be nonempty"),
    ],
)
def test_collected_event_rejects_blank_identity_fields(
    field: str, value: str, message: str
) -> None:
    payload = collected_event().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        CollectedEvent.model_validate(payload)


def test_collected_event_rejects_quote_source_or_event_mismatch() -> None:
    mismatched_source = collected_event().model_dump()
    mismatched_source["quotes"][Outcome.HOME] = Quote(
        source=Source.SOURCE_B,
        event_id="30549576",
        outcome=Outcome.HOME,
        odds="1.4700",
        observed_at=OBSERVED_AT,
    )
    with pytest.raises(ValidationError, match="quote source must match event source"):
        CollectedEvent.model_validate(mismatched_source)

    mismatched_id = collected_event().model_dump()
    mismatched_id["quotes"][Outcome.HOME] = Quote(
        source=Source.SOURCE_A,
        event_id="different",
        outcome=Outcome.HOME,
        odds="1.4700",
        observed_at=OBSERVED_AT,
    )
    with pytest.raises(ValidationError, match="quote event_id must match source_event_id"):
        CollectedEvent.model_validate(mismatched_id)

    mismatched_outcome = collected_event().model_dump()
    mismatched_outcome["quotes"][Outcome.HOME] = quote(Outcome.DRAW, "1.4700")
    with pytest.raises(ValidationError, match="quote keys must match quote outcomes"):
        CollectedEvent.model_validate(mismatched_outcome)


def test_collection_batch_rejects_invalid_window_or_foreign_event() -> None:
    event = collected_event()
    with pytest.raises(ValidationError, match="finished_at must not be earlier"):
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=OBSERVED_AT,
            finished_at=OBSERVED_AT - timedelta(seconds=1),
            events=(event,),
        )

    foreign_payload = event.model_dump()
    foreign_payload["source"] = Source.SOURCE_B
    foreign_payload["quotes"] = {
        outcome: Quote(
            source=Source.SOURCE_B,
            event_id="30549576",
            outcome=outcome,
            odds=odds,
            observed_at=OBSERVED_AT,
        )
        for outcome, odds in (
            (Outcome.HOME, "1.4700"),
            (Outcome.DRAW, "4.3300"),
            (Outcome.AWAY, "4.6000"),
        )
    }
    foreign = CollectedEvent.model_validate(foreign_payload)
    with pytest.raises(ValidationError, match="event source must match batch source"):
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=OBSERVED_AT,
            finished_at=OBSERVED_AT,
            events=(foreign,),
        )


def test_collection_models_require_timezone_aware_datetimes() -> None:
    event_payload = collected_event().model_dump()
    event_payload["kickoff"] = KICKOFF.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone-aware"):
        CollectedEvent.model_validate(event_payload)

    with pytest.raises(ValidationError, match="timezone-aware"):
        CollectionBatch(
            source=Source.SOURCE_A,
            started_at=OBSERVED_AT.replace(tzinfo=None),
            finished_at=OBSERVED_AT,
            events=(collected_event(),),
        )

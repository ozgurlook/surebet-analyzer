"""Immutable, source-neutral records returned by sportsbook adapters."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from pydantic import Field, StrictStr, field_serializer, field_validator, model_validator

from surebet.domain.enums import Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import DomainModel, Quote
from surebet.taxonomy.models import SourceCompetitionEvidence

FULL_TIME_RESULT_MARKET: Literal["football.full_time_result.90m"] = "football.full_time_result.90m"


class SourceUnavailable(RuntimeError):
    """A public source could not produce a collection batch."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _nonempty(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must be nonempty")
    return value


class CollectedEvent(DomainModel):
    source: Source
    source_event_id: StrictStr
    competition: StrictStr
    home: StrictStr
    away: StrictStr
    kickoff: datetime
    market_key: str = FULL_TIME_RESULT_MARKET
    quotes: Mapping[Outcome, Quote] = Field(default_factory=dict)
    ou25_quotes: Mapping[TwoWayOutcome, Quote] | None = None
    sport: Sport = Sport.FOOTBALL
    bask_ml_quotes: Mapping[Outcome, Quote] | None = None
    bask_ah_quotes: Mapping[tuple[Outcome, Decimal], Quote] | None = None
    source_competition: SourceCompetitionEvidence | None = None

    @field_validator("source_event_id", "competition", "home", "away")
    @classmethod
    def validate_identity(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "value"))
        return _nonempty(value, field_name)

    _validate_kickoff = field_validator("kickoff")(_aware)

    @field_validator("quotes")
    @classmethod
    def validate_quotes(cls, value: Mapping[Outcome, Quote]) -> Mapping[Outcome, Quote]:
        return MappingProxyType(dict(value))

    @field_validator("ou25_quotes")
    @classmethod
    def validate_ou25_quotes(
        cls, value: Mapping[TwoWayOutcome, Quote] | None
    ) -> Mapping[TwoWayOutcome, Quote] | None:
        if value is None:
            return None
        expected = {TwoWayOutcome.OVER, TwoWayOutcome.UNDER}
        if set(value) != expected:
            raise ValueError("ou25_quotes must contain exactly OVER and UNDER")
        return MappingProxyType(dict(value))

    @field_validator("bask_ml_quotes")
    @classmethod
    def validate_bask_ml_quotes(
        cls, value: Mapping[Outcome, Quote] | None
    ) -> Mapping[Outcome, Quote] | None:
        if not value:
            return None
        return MappingProxyType(dict(value))

    @field_validator("bask_ah_quotes", mode="before")
    @classmethod
    def validate_bask_ah_quotes(
        cls, value: Mapping[Any, Any] | None
    ) -> Mapping[tuple[Outcome, Decimal], Quote] | None:
        if not value:
            return None
        converted: dict[tuple[Outcome, Decimal], Quote] = {}
        for k, v in value.items():
            if isinstance(k, tuple):
                converted[k] = v
            elif isinstance(k, str) and ":" in k:
                outcome_str, line_str = k.split(":", 1)
                converted[(Outcome(outcome_str), Decimal(line_str))] = v
            else:
                converted[k] = v
        return MappingProxyType(converted)

    @field_serializer("quotes")
    def serialize_quotes(self, value: Mapping[Outcome, Quote]) -> dict[Outcome, Quote]:
        return dict(value)

    @field_serializer("ou25_quotes")
    def serialize_ou25_quotes(
        self, value: Mapping[TwoWayOutcome, Quote] | None
    ) -> dict[TwoWayOutcome, Quote] | None:
        return dict(value) if value is not None else None

    @field_serializer("bask_ml_quotes")
    def serialize_bask_ml_quotes(
        self, value: Mapping[Outcome, Quote] | None
    ) -> dict[Outcome, Quote] | None:
        return dict(value) if value is not None else None

    @field_serializer("bask_ah_quotes")
    def serialize_bask_ah_quotes(
        self, value: Mapping[tuple[Outcome, Decimal], Quote] | None
    ) -> dict[str, Quote] | None:
        if value is None:
            return None
        return {f"{k[0].value}:{k[1]}": v for k, v in value.items()}

    @model_validator(mode="after")
    def validate_quote_identity(self) -> "CollectedEvent":
        if (
            self.source_competition is not None
            and self.source_competition.provider != self.source.value
        ):
            raise ValueError("source competition provider must match event source")

        if self.sport is Sport.FOOTBALL and set(self.quotes) != set(Outcome):
            raise ValueError("quotes must contain exactly HOME, DRAW, and AWAY")
        elif self.sport is Sport.BASKETBALL and not self.bask_ml_quotes and not self.bask_ah_quotes:
            raise ValueError("basketball event must contain bask_ml_quotes or bask_ah_quotes")

        for quote in self.quotes.values():
            if quote.source is not self.source:
                raise ValueError("quote source must match event source")
            if quote.event_id != self.source_event_id:
                raise ValueError("quote event_id must match source_event_id")
        if any(key != quote.outcome for key, quote in self.quotes.items()):
            raise ValueError("quote keys must match quote outcomes")

        if self.ou25_quotes is not None:
            for quote in self.ou25_quotes.values():
                if quote.source is not self.source:
                    raise ValueError("quote source must match event source")
                if quote.event_id != self.source_event_id:
                    raise ValueError("quote event_id must match source_event_id")
            if any(key != quote.outcome for key, quote in self.ou25_quotes.items()):
                raise ValueError("quote keys must match quote outcomes")

        if self.bask_ml_quotes is not None:
            for quote in self.bask_ml_quotes.values():
                if quote.source is not self.source:
                    raise ValueError("quote source must match event source")
                if quote.event_id != self.source_event_id:
                    raise ValueError("quote event_id must match source_event_id")
            if any(key != quote.outcome for key, quote in self.bask_ml_quotes.items()):
                raise ValueError("quote keys must match quote outcomes")

        if self.bask_ah_quotes is not None:
            for (outcome, line), quote in self.bask_ah_quotes.items():
                if quote.source is not self.source:
                    raise ValueError("quote source must match event source")
                if quote.event_id != self.source_event_id:
                    raise ValueError("quote event_id must match source_event_id")
                if quote.outcome != outcome or quote.line != line:
                    raise ValueError("quote keys must match quote outcomes and lines")

        return self


class CollectionBatch(DomainModel):
    source: Source
    started_at: datetime
    finished_at: datetime
    events: tuple[CollectedEvent, ...]

    _validate_started_at = field_validator("started_at")(_aware)
    _validate_finished_at = field_validator("finished_at")(_aware)

    @model_validator(mode="after")
    def validate_batch(self) -> "CollectionBatch":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        if any(event.source is not self.source for event in self.events):
            raise ValueError("event source must match batch source")
        return self

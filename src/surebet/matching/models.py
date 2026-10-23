"""Models for normalized matched football events across sources."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_serializer, field_validator

from surebet.domain.enums import Outcome, Source, Sport, TwoWayOutcome
from surebet.domain.models import Quote
from surebet.taxonomy.models import TaxonomyStatus


class MatchedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched_id: StrictStr
    taxonomy_status: TaxonomyStatus
    competition: StrictStr
    country_id: StrictStr | None
    country_name: StrictStr | None
    country_iso_code: StrictStr | None
    league_id: StrictStr | None
    league_name: StrictStr | None
    home: StrictStr
    away: StrictStr
    kickoff: datetime
    source_events: Mapping[Source, StrictStr]  # Source -> source_event_id
    quotes: Mapping[tuple[Source, Outcome], Quote]
    ou25_quotes: Mapping[tuple[Source, TwoWayOutcome], Quote] = Field(default_factory=dict)
    sport: Sport = Sport.FOOTBALL
    bask_ml_quotes: Mapping[tuple[Source, Outcome], Quote] = Field(default_factory=dict)
    bask_ah_quotes: Mapping[tuple[Source, Outcome, Decimal], Quote] = Field(default_factory=dict)

    @field_validator("source_events")
    @classmethod
    def _validate_source_events(cls, value: Mapping[Source, str]) -> Mapping[Source, str]:
        return MappingProxyType(dict(value))

    @field_validator("quotes")
    @classmethod
    def _validate_quotes(
        cls, value: Mapping[tuple[Source, Outcome], Quote]
    ) -> Mapping[tuple[Source, Outcome], Quote]:
        return MappingProxyType(dict(value))

    @field_validator("ou25_quotes", mode="before")
    @classmethod
    def _validate_2way_quotes(
        cls, value: Mapping[tuple[Source, TwoWayOutcome], Quote] | None
    ) -> Mapping[tuple[Source, TwoWayOutcome], Quote]:
        if not value:
            return MappingProxyType({})
        return MappingProxyType(dict(value))

    @field_validator("bask_ml_quotes", mode="before")
    @classmethod
    def _validate_bask_ml_quotes(
        cls, value: Mapping[tuple[Source, Outcome], Quote] | None
    ) -> Mapping[tuple[Source, Outcome], Quote]:
        if not value:
            return MappingProxyType({})
        return MappingProxyType(dict(value))

    @field_validator("bask_ah_quotes", mode="before")
    @classmethod
    def _validate_bask_ah_quotes(
        cls, value: Mapping[tuple[Source, Outcome, Decimal], Quote] | None
    ) -> Mapping[tuple[Source, Outcome, Decimal], Quote]:
        if not value:
            return MappingProxyType({})
        return MappingProxyType(dict(value))

    @field_serializer("source_events")
    def _serialize_source_events(self, value: Mapping[Source, str]) -> dict[str, str]:
        return {k.value: v for k, v in value.items()}

    @field_serializer("quotes")
    def _serialize_quotes(self, value: Mapping[tuple[Source, Outcome], Quote]) -> dict[str, Quote]:
        return {f"{k[0].value}:{k[1].value}": v for k, v in value.items()}

    @field_serializer("ou25_quotes")
    def _serialize_ou25_quotes(
        self, value: Mapping[tuple[Source, TwoWayOutcome], Quote]
    ) -> dict[str, Quote]:
        return {f"{k[0].value}:{k[1].value}": v for k, v in value.items()}

    @field_serializer("bask_ml_quotes")
    def _serialize_bask_ml_quotes(
        self, value: Mapping[tuple[Source, Outcome], Quote]
    ) -> dict[str, Quote]:
        return {f"{k[0].value}:{k[1].value}": v for k, v in value.items()}

    @field_serializer("bask_ah_quotes")
    def _serialize_bask_ah_quotes(
        self, value: Mapping[tuple[Source, Outcome, Decimal], Quote]
    ) -> dict[str, Quote]:
        return {f"{k[0].value}:{k[1].value}:{k[2]}": v for k, v in value.items()}

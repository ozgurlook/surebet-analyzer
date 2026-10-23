"""Immutable source and canonical taxonomy records."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, NamedTuple, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from surebet.domain.enums import Source
from surebet.domain.models import DomainModel

Confidence = Annotated[float, Field(ge=0.0, le=1.0, strict=True)]
Gender = Literal["men", "women"]
_ISO_ALPHA2_CODES = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
    BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW
    CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF
    GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ
    IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU
    LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC
    NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE
    RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD
    TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU
    WF WS YE YT ZA ZM ZW""".split()  # noqa: SIM905 - compact auditable ISO list
)


def _nonempty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be nonempty")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _valid_region_id(value: str) -> bool:
    if value in {"region:international", "region:unknown"}:
        return True
    prefix, separator, iso_code = value.partition(":")
    return prefix == "country" and separator == ":" and iso_code in _ISO_ALPHA2_CODES


def _validate_league_id(value: str) -> str:
    _nonempty(value, "league_id")
    if not value.startswith("league:"):
        raise ValueError("league_id must use the league:<uuid4> format")
    try:
        parsed = UUID(value.removeprefix("league:"))
    except ValueError as error:
        raise ValueError("league_id must use the league:<uuid4> format") from error
    if parsed.version != 4 or str(parsed) != value.removeprefix("league:"):
        raise ValueError("league_id must use the league:<uuid4> format")
    return value


class SourceIdentityKind(StrEnum):
    PROVIDER_ID = "provider_id"
    DERIVED_TEXT = "derived_text"


class CompetitionKind(StrEnum):
    LEAGUE = "league"
    CUP = "cup"
    QUALIFIER = "qualifier"
    FRIENDLY = "friendly"
    INTERNATIONAL_CLUB = "international_club"
    INTERNATIONAL_NATIONAL = "international_national"
    UNKNOWN = "unknown"


class RegionClass(StrEnum):
    COUNTRY = "country"
    INTERNATIONAL = "international"
    UNKNOWN = "unknown"


class MappingMethod(StrEnum):
    MANUAL = "manual"
    TRACKED_REGISTRY = "tracked_registry"
    VERIFIED_SOURCE_ID = "verified_source_id"
    EXACT_ALIAS = "exact_alias"
    SUGGESTION = "suggestion"


class ReviewState(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    CONFLICT = "conflict"


class TaxonomyStatus(StrEnum):
    VERIFIED = "verified"
    UNMAPPED = "unmapped"


class LeagueSignature(NamedTuple):
    normalized_name: str
    kind: CompetitionKind
    gender: str | None
    age_group: str | None
    is_reserve: bool | None


class SourceCompetitionEvidence(DomainModel):
    provider: StrictStr
    identity_kind: SourceIdentityKind
    competition_id: StrictStr
    competition_name: StrictStr
    country_id: StrictStr | None = None
    country_name: StrictStr | None = None
    country_alias: StrictStr | None = None
    season: StrictStr | None = None
    kind: CompetitionKind = CompetitionKind.UNKNOWN
    gender: Gender | None = None
    age_group: StrictStr | None = None
    is_reserve: StrictBool | None = None

    @field_validator("provider", "competition_id", "competition_name")
    @classmethod
    def validate_identity(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "value"))
        return _nonempty(value, field_name)


class CanonicalRegion(DomainModel):
    region_id: StrictStr
    region_class: RegionClass
    iso_code: StrictStr | None
    display_name: StrictStr
    normalized_key: StrictStr
    active: StrictBool = True

    @field_validator("region_id", "display_name", "normalized_key")
    @classmethod
    def validate_stable_text(cls, value: str, info: object) -> str:
        return _nonempty(value, str(getattr(info, "field_name", "value")))

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.region_class is RegionClass.COUNTRY:
            if self.iso_code not in _ISO_ALPHA2_CODES:
                raise ValueError("country regions require an uppercase ISO alpha-2 code")
            if self.region_id != f"country:{self.iso_code}":
                raise ValueError("country region_id must match iso_code")
        elif self.iso_code is not None:
            raise ValueError("special regions must not have an ISO code")
        elif self.region_id != f"region:{self.region_class.value}":
            raise ValueError("special region_id must match region_class")
        return self


class CanonicalLeague(DomainModel):
    league_id: StrictStr
    region_id: StrictStr
    display_name: StrictStr
    normalized_key: StrictStr
    kind: CompetitionKind
    gender: Gender | None = None
    age_group: StrictStr | None = None
    is_reserve: StrictBool | None = None
    active: StrictBool = True

    _validate_id = field_validator("league_id")(_validate_league_id)

    @field_validator("region_id")
    @classmethod
    def validate_region_id(cls, value: str) -> str:
        if not _valid_region_id(value):
            raise ValueError("region_id must use a canonical region ID")
        return value

    @field_validator("display_name", "normalized_key")
    @classmethod
    def validate_stable_text(cls, value: str, info: object) -> str:
        return _nonempty(value, str(getattr(info, "field_name", "value")))

    @field_validator("age_group")
    @classmethod
    def validate_age_group(cls, value: str | None) -> str | None:
        return None if value is None else _nonempty(value, "age_group")


class SourceCompetition(SourceCompetitionEvidence):
    source: Source
    first_seen_at: datetime
    last_seen_at: datetime

    _validate_first_seen_at = field_validator("first_seen_at")(_aware)
    _validate_last_seen_at = field_validator("last_seen_at")(_aware)

    @model_validator(mode="after")
    def validate_source_and_observation_range(self) -> Self:
        if self.provider != self.source.value:
            raise ValueError("provider must match source")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at must not be earlier than first_seen_at")
        return self


class CompetitionMapping(DomainModel):
    source: Source
    competition_id: StrictStr
    league_id: StrictStr
    method: MappingMethod
    confidence: Confidence
    review_state: ReviewState
    note: StrictStr | None = None
    created_at: datetime
    updated_at: datetime

    _validate_league_id = field_validator("league_id")(_validate_league_id)
    _validate_created_at = field_validator("created_at")(_aware)
    _validate_updated_at = field_validator("updated_at")(_aware)

    @field_validator("competition_id")
    @classmethod
    def validate_competition_id(cls, value: str) -> str:
        return _nonempty(value, "competition_id")

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        return None if value is None else _nonempty(value, "note")

    @model_validator(mode="after")
    def validate_timestamp_range(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self


class ResolvedCompetition(DomainModel):
    source: Source
    competition_id: StrictStr
    region: CanonicalRegion
    league: CanonicalLeague
    method: MappingMethod
    confidence: Confidence

    @field_validator("competition_id")
    @classmethod
    def validate_competition_id(cls, value: str) -> str:
        return _nonempty(value, "competition_id")

    @model_validator(mode="after")
    def validate_region_relationship(self) -> Self:
        if self.league.region_id != self.region.region_id:
            raise ValueError("league region_id must match the resolved region")
        return self


class CreateLeagueInput(DomainModel):
    display_name: StrictStr
    kind: CompetitionKind
    gender: Gender | None = None
    age_group: StrictStr | None = None
    is_reserve: StrictBool | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return _nonempty(value, "display_name")

    @field_validator("age_group")
    @classmethod
    def validate_age_group(cls, value: str | None) -> str | None:
        return None if value is None else _nonempty(value, "age_group")

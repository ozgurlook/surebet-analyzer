from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from surebet.domain.enums import Source
from surebet.taxonomy.models import (
    CanonicalLeague,
    CanonicalRegion,
    CompetitionKind,
    CompetitionMapping,
    CreateLeagueInput,
    LeagueSignature,
    MappingMethod,
    RegionClass,
    ResolvedCompetition,
    ReviewState,
    SourceCompetition,
    SourceCompetitionEvidence,
    SourceIdentityKind,
    TaxonomyStatus,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
NAIVE = datetime.fromisoformat("2026-08-26T00:00:00")


def _turkey() -> CanonicalRegion:
    return CanonicalRegion(
        region_id="country:TR",
        region_class=RegionClass.COUNTRY,
        iso_code="TR",
        display_name="T\u00fcrkiye",
        normalized_key="t\u00fcrkiye",
        active=True,
    )


def _league() -> CanonicalLeague:
    return CanonicalLeague(
        league_id=f"league:{uuid4()}",
        region_id="country:TR",
        display_name="S\u00fcper Lig",
        normalized_key="s\u00fcper lig",
        kind=CompetitionKind.LEAGUE,
        gender="men",
        age_group=None,
        is_reserve=False,
        active=True,
    )


def _evidence() -> SourceCompetitionEvidence:
    return SourceCompetitionEvidence(
        provider="source_a",
        identity_kind=SourceIdentityKind.PROVIDER_ID,
        competition_id="42",
        competition_name="S\u00fcper Lig",
        country_id="TR",
        country_name="T\u00fcrkiye",
        kind=CompetitionKind.LEAGUE,
        gender="men",
        is_reserve=False,
    )


def test_league_signature_keeps_all_safety_qualifiers() -> None:
    signatures = {
        LeagueSignature("super lig", CompetitionKind.LEAGUE, "men", None, False),
        LeagueSignature("super lig", CompetitionKind.LEAGUE, "women", None, False),
        LeagueSignature("super lig", CompetitionKind.LEAGUE, "men", "U21", False),
        LeagueSignature("super lig", CompetitionKind.LEAGUE, "men", None, True),
        LeagueSignature("super lig", CompetitionKind.CUP, "men", None, False),
        LeagueSignature("super lig", CompetitionKind.QUALIFIER, "men", None, False),
        LeagueSignature("super lig", CompetitionKind.FRIENDLY, "men", None, False),
        LeagueSignature("super lig", CompetitionKind.INTERNATIONAL_CLUB, "men", None, False),
        LeagueSignature("super lig", CompetitionKind.INTERNATIONAL_NATIONAL, "men", None, False),
    }

    assert len(signatures) == 9


def test_shared_enum_values_are_stable() -> None:
    assert RegionClass.COUNTRY.value == "country"
    assert MappingMethod.VERIFIED_SOURCE_ID.value == "verified_source_id"
    assert ReviewState.CONFLICT.value == "conflict"
    assert TaxonomyStatus.UNMAPPED.value == "unmapped"


def test_canonical_region_accepts_country_and_special_identities() -> None:
    country = _turkey()
    international = CanonicalRegion(
        region_id="region:international",
        region_class=RegionClass.INTERNATIONAL,
        iso_code=None,
        display_name="Uluslararas\u0131",
        normalized_key="uluslararas\u0131",
        active=True,
    )

    assert country.iso_code == "TR"
    assert international.iso_code is None


@pytest.mark.parametrize(
    ("region_id", "region_class", "iso_code"),
    [
        ("country:TR", RegionClass.COUNTRY, None),
        ("country:DE", RegionClass.COUNTRY, "TR"),
        ("country:ZZ", RegionClass.COUNTRY, "ZZ"),
        ("region:international", RegionClass.INTERNATIONAL, "TR"),
        ("region:unknown", RegionClass.INTERNATIONAL, None),
        ("TR", RegionClass.COUNTRY, "TR"),
    ],
)
def test_canonical_region_rejects_inconsistent_identity(
    region_id: str, region_class: RegionClass, iso_code: str | None
) -> None:
    with pytest.raises(ValidationError):
        CanonicalRegion(
            region_id=region_id,
            region_class=region_class,
            iso_code=iso_code,
            display_name="T\u00fcrkiye",
            normalized_key="t\u00fcrkiye",
            active=True,
        )


@pytest.mark.parametrize("field", ["display_name", "normalized_key"])
def test_canonical_region_rejects_blank_stable_text(field: str) -> None:
    payload = _turkey().model_dump()
    payload[field] = "   "

    with pytest.raises(ValidationError):
        CanonicalRegion.model_validate(payload)


def test_canonical_league_requires_uuid4_id_and_valid_region_id() -> None:
    valid = _league()
    assert UUID(valid.league_id.removeprefix("league:")).version == 4

    for field, value in (
        ("league_id", "league:123"),
        ("region_id", "TR"),
        ("region_id", "country:ZZ"),
    ):
        payload = valid.model_dump()
        payload[field] = value
        with pytest.raises(ValidationError):
            CanonicalLeague.model_validate(payload)


@pytest.mark.parametrize("field", ["display_name", "normalized_key"])
def test_canonical_league_rejects_blank_stable_text(field: str) -> None:
    payload = _league().model_dump()
    payload[field] = "\t"

    with pytest.raises(ValidationError):
        CanonicalLeague.model_validate(payload)


def test_source_competition_preserves_evidence_and_observation_range() -> None:
    source_competition = SourceCompetition(
        source=Source.SOURCE_A,
        **_evidence().model_dump(),
        first_seen_at=NOW,
        last_seen_at=NOW,
    )

    assert source_competition.competition_id == "42"
    assert source_competition.country_name == "T\u00fcrkiye"


@pytest.mark.parametrize(
    ("source", "provider", "first_seen_at", "last_seen_at"),
    [
        (Source.SOURCE_A, "source_c", NOW, NOW),
        (Source.SOURCE_A, "source_a", NAIVE, NOW),
        (Source.SOURCE_A, "source_a", NOW, NAIVE),
        (Source.SOURCE_A, "source_a", NOW, datetime(2026, 8, 25, tzinfo=UTC)),
    ],
)
def test_source_competition_rejects_invalid_source_or_observation_range(
    source: Source, provider: str, first_seen_at: datetime, last_seen_at: datetime
) -> None:
    with pytest.raises(ValidationError):
        SourceCompetition(
            source=source,
            **(_evidence().model_dump() | {"provider": provider}),
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
        )


def test_competition_mapping_preserves_review_metadata() -> None:
    league = _league()
    mapping = CompetitionMapping(
        source=Source.SOURCE_A,
        competition_id="42",
        league_id=league.league_id,
        method=MappingMethod.MANUAL,
        confidence=1.0,
        review_state=ReviewState.APPROVED,
        note="verified against provider",
        created_at=NOW,
        updated_at=NOW,
    )

    assert mapping.note == "verified against provider"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_competition_mapping_rejects_confidence_outside_unit_interval(
    confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        CompetitionMapping(
            source=Source.SOURCE_A,
            competition_id="42",
            league_id=_league().league_id,
            method=MappingMethod.SUGGESTION,
            confidence=confidence,
            review_state=ReviewState.PENDING,
            note=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_competition_mapping_rejects_blank_key_and_invalid_timestamps() -> None:
    league = _league()
    base = {
        "source": Source.SOURCE_A,
        "competition_id": "42",
        "league_id": league.league_id,
        "method": MappingMethod.MANUAL,
        "confidence": 1.0,
        "review_state": ReviewState.APPROVED,
        "note": None,
        "created_at": NOW,
        "updated_at": NOW,
    }

    for changes in (
        {"competition_id": " "},
        {"league_id": "league:123"},
        {"created_at": NAIVE},
        {"updated_at": datetime(2026, 8, 25, tzinfo=UTC)},
    ):
        with pytest.raises(ValidationError):
            CompetitionMapping.model_validate(base | changes)


def test_resolved_competition_requires_matching_region_and_bounded_confidence() -> None:
    region = _turkey()
    league = _league()
    resolved = ResolvedCompetition(
        source=Source.SOURCE_A,
        competition_id="42",
        region=region,
        league=league,
        method=MappingMethod.VERIFIED_SOURCE_ID,
        confidence=0.98,
    )
    assert resolved.league.region_id == resolved.region.region_id

    mismatched = league.model_copy(update={"region_id": "country:DE"})
    with pytest.raises(ValidationError):
        ResolvedCompetition(
            source=Source.SOURCE_A,
            competition_id="42",
            region=region,
            league=mismatched,
            method=MappingMethod.EXACT_ALIAS,
            confidence=0.9,
        )

    with pytest.raises(ValidationError):
        resolved.model_copy(update={"confidence": 2.0}).__class__.model_validate(
            resolved.model_dump() | {"confidence": 2.0}
        )


def test_create_league_input_keeps_identity_qualifiers() -> None:
    league_input = CreateLeagueInput(
        display_name="Kad\u0131nlar S\u00fcper Ligi",
        kind=CompetitionKind.LEAGUE,
        gender="women",
        age_group=None,
        is_reserve=False,
    )

    assert league_input.gender == "women"
    assert league_input.is_reserve is False

    with pytest.raises(ValidationError):
        CreateLeagueInput(
            display_name=" ",
            kind=CompetitionKind.LEAGUE,
            gender="women",
            age_group=None,
            is_reserve=False,
        )

"""Authenticated, CSRF-protected taxonomy mapping administration APIs.

Every mutation carries the caller's `expected_updated_at`, so a stale review
screen can never overwrite a newer decision: the repository raises
`TaxonomyConflict` and this module answers HTTP 409. Country, competition kind,
gender, age group, and reserve-state conflicts are hard repository rejections
that surface as HTTP 422 and never as a silently accepted mapping.

Responses expose normalized taxonomy records only - never the raw provider
payload, cookies, or tokens.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from surebet.domain.enums import Source
from surebet.storage.auth import SessionData
from surebet.storage.taxonomy import LeagueState, TaxonomyConflict, TaxonomyRepository
from surebet.taxonomy.models import (
    CompetitionKind,
    CompetitionMapping,
    CreateLeagueInput,
    Gender,
    MappingMethod,
    ReviewState,
    SourceCompetition,
    SourceIdentityKind,
)
from surebet.taxonomy.normalizer import CountryDictionary
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(tags=["mappings"])

_MAX_UNMATCHED_LIMIT = 200

DERIVED_TEXT_IDENTITY = "derived_text_identity"
UNRESOLVED_COUNTRY = "unresolved_country"
AMBIGUOUS_COUNTRY = "ambiguous_country"


@lru_cache(maxsize=1)
def _countries() -> CountryDictionary:
    return CountryDictionary.load_packaged()


class MappingTarget(BaseModel):
    """The canonical league a review item currently points at."""

    league_id: str
    region_id: str
    display_name: str
    kind: CompetitionKind
    gender: str | None
    age_group: str | None
    is_reserve: bool | None
    active: bool
    updated_at: datetime


class UnmatchedItem(BaseModel):
    """One manual-review queue entry: normalized evidence plus review context."""

    source: Source
    competition_id: str
    identity_kind: SourceIdentityKind
    competition_name: str
    country_id: str | None
    country_name: str | None
    country_alias: str | None
    season: str | None
    kind: CompetitionKind
    gender: str | None
    age_group: str | None
    is_reserve: bool | None
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    method: MappingMethod | None
    confidence: float | None
    review_state: ReviewState | None
    target: MappingTarget | None
    conflict_reasons: list[str]


class MappingItem(BaseModel):
    """The persisted mapping after a successful mutation."""

    source: Source
    competition_id: str
    league_id: str
    method: MappingMethod
    confidence: float
    review_state: ReviewState
    note: str | None
    created_at: datetime
    updated_at: datetime


class LeagueRecord(BaseModel):
    """A canonical league after a successful rename."""

    league_id: str
    region_id: str
    display_name: str
    kind: CompetitionKind
    gender: str | None
    age_group: str | None
    is_reserve: bool | None
    active: bool
    updated_at: datetime


class _MutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime


class ApproveRequest(_MutationRequest):
    note: StrictStr | None = None


class LinkRequest(_MutationRequest):
    league_id: StrictStr
    note: StrictStr | None = None


class CreateLeagueRequest(_MutationRequest):
    region_id: StrictStr
    display_name: StrictStr
    kind: CompetitionKind
    gender: Gender | None = None
    age_group: StrictStr | None = None
    is_reserve: StrictBool | None = None
    note: StrictStr | None = None

    def to_league_input(self) -> CreateLeagueInput:
        return CreateLeagueInput(
            display_name=self.display_name,
            kind=self.kind,
            gender=self.gender,
            age_group=self.age_group,
            is_reserve=self.is_reserve,
        )


class RenameLeagueRequest(_MutationRequest):
    display_name: StrictStr


@contextmanager
def _translated_errors() -> Iterator[None]:
    """Map repository failures onto their HTTP meaning."""
    try:
        yield
    except TaxonomyConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from None


def _repo(request: Request) -> TaxonomyRepository:
    return request.app.state.taxonomy_repo  # type: ignore[no-any-return]


def _competition_id(value: str) -> str:
    """Validate an opaque, already percent-decoded provider competition identifier.

    Starlette decodes path parameters once, so `text:`-derived identifiers keep
    their spaces and non-ASCII characters here and must not be decoded again.
    """
    if not value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="competition_id must be nonempty",
        )
    return value


def _conflict_reasons(row: SourceCompetition) -> list[str]:
    """Explain why the observation could not verify itself automatically."""
    reasons: list[str] = []
    if row.identity_kind is SourceIdentityKind.DERIVED_TEXT:
        reasons.append(DERIVED_TEXT_IDENTITY)
    resolved = {
        region.region_id
        for label in (row.country_id, row.country_name, row.country_alias)
        if label and (region := _countries().resolve(label)) is not None
    }
    if not resolved:
        reasons.append(UNRESOLVED_COUNTRY)
    elif len(resolved) > 1:
        reasons.append(AMBIGUOUS_COUNTRY)
    return reasons


def _target(state: LeagueState) -> MappingTarget:
    return MappingTarget(
        league_id=state.league.league_id,
        region_id=state.league.region_id,
        display_name=state.league.display_name,
        kind=state.league.kind,
        gender=state.league.gender,
        age_group=state.league.age_group,
        is_reserve=state.league.is_reserve,
        active=state.league.active,
        updated_at=state.updated_at,
    )


def _mapping_item(mapping: CompetitionMapping) -> MappingItem:
    return MappingItem(
        source=mapping.source,
        competition_id=mapping.competition_id,
        league_id=mapping.league_id,
        method=mapping.method,
        confidence=mapping.confidence,
        review_state=mapping.review_state,
        note=mapping.note,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _league_record(repo: TaxonomyRepository, league_id: str) -> LeagueRecord:
    state = repo.get_league(league_id)
    if state is None:  # pragma: no cover - the league was just written
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="league not found")
    return LeagueRecord(**_target(state).model_dump())


def _unmatched_item(
    repo: TaxonomyRepository, row: SourceCompetition, targets: dict[str, LeagueState | None]
) -> UnmatchedItem:
    mapping = repo.get_mapping(row.source, row.competition_id)
    target: LeagueState | None = None
    if mapping is not None:
        if mapping.league_id not in targets:
            targets[mapping.league_id] = repo.get_league(mapping.league_id)
        target = targets[mapping.league_id]
    return UnmatchedItem(
        source=row.source,
        competition_id=row.competition_id,
        identity_kind=row.identity_kind,
        competition_name=row.competition_name,
        country_id=row.country_id,
        country_name=row.country_name,
        country_alias=row.country_alias,
        season=row.season,
        kind=row.kind,
        gender=row.gender,
        age_group=row.age_group,
        is_reserve=row.is_reserve,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        # The optimistic version every mutation on this item must echo back.
        updated_at=mapping.updated_at if mapping is not None else row.last_seen_at,
        method=mapping.method if mapping is not None else None,
        confidence=mapping.confidence if mapping is not None else None,
        review_state=mapping.review_state if mapping is not None else None,
        target=None if target is None else _target(target),
        conflict_reasons=_conflict_reasons(row),
    )


@router.get("/mappings/unmatched", response_model=list[UnmatchedItem])
def list_unmatched(
    request: Request,
    limit: Annotated[int, Query(ge=1)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _auth: None = Depends(require_authenticated_session),
) -> list[UnmatchedItem]:
    repo = _repo(request)
    rows = repo.list_unmatched(limit=min(limit, _MAX_UNMATCHED_LIMIT), offset=offset)
    targets: dict[str, LeagueState | None] = {}
    return [_unmatched_item(repo, row, targets) for row in rows]


@router.get("/mappings/mapped", response_model=list[UnmatchedItem])
def list_mapped(
    request: Request,
    limit: Annotated[int, Query(ge=1)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _auth: None = Depends(require_authenticated_session),
) -> list[UnmatchedItem]:
    repo = _repo(request)
    rows = repo.list_mapped(limit=min(limit, _MAX_UNMATCHED_LIMIT), offset=offset)
    targets: dict[str, LeagueState | None] = {}
    return [_unmatched_item(repo, row, targets) for row in rows]


@router.post("/mappings/{source}/{competition_id}/approve", response_model=MappingItem)
def approve_suggestion(
    source: Source,
    competition_id: str,
    payload: ApproveRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> MappingItem:
    with _translated_errors():
        mapping = _repo(request).approve_suggestion(
            source,
            _competition_id(competition_id),
            expected_updated_at=payload.expected_updated_at,
            actor=session.username,
            note=payload.note,
        )
    return _mapping_item(mapping)


@router.put("/mappings/{source}/{competition_id}/link", response_model=MappingItem)
def link_league(
    source: Source,
    competition_id: str,
    payload: LinkRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> MappingItem:
    with _translated_errors():
        mapping = _repo(request).link_manual(
            source,
            _competition_id(competition_id),
            payload.league_id,
            expected_updated_at=payload.expected_updated_at,
            actor=session.username,
            note=payload.note,
        )
    return _mapping_item(mapping)


@router.post(
    "/mappings/{source}/{competition_id}/league",
    response_model=MappingItem,
    status_code=status.HTTP_201_CREATED,
)
def create_and_link_league(
    source: Source,
    competition_id: str,
    payload: CreateLeagueRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> MappingItem:
    with _translated_errors():
        mapping = _repo(request).create_and_link(
            source,
            _competition_id(competition_id),
            payload.region_id,
            payload.to_league_input(),
            expected_updated_at=payload.expected_updated_at,
            actor=session.username,
            note=payload.note,
        )
    return _mapping_item(mapping)


@router.patch("/leagues/{league_id}", response_model=LeagueRecord)
def rename_league(
    league_id: str,
    payload: RenameLeagueRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> LeagueRecord:
    repo = _repo(request)
    with _translated_errors():
        repo.rename_league(
            league_id,
            payload.display_name,
            expected_updated_at=payload.expected_updated_at,
            actor=session.username,
        )
    return _league_record(repo, league_id)


@router.delete("/mappings/{source}/{competition_id}", status_code=status.HTTP_204_NO_CONTENT)
def undo_mapping(
    source: Source,
    competition_id: str,
    request: Request,
    expected_updated_at: Annotated[datetime, Query()],
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> Response:
    with _translated_errors():
        _repo(request).undo_mapping(
            source,
            _competition_id(competition_id),
            expected_updated_at=expected_updated_at,
            actor=session.username,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

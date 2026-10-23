"""Country and league catalog APIs backed by CatalogService."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from surebet.catalog.service import CatalogService, CatalogSnapshot
from surebet.domain.models import Quote
from surebet.matching.models import MatchedEvent
from surebet.opportunities.strategy import calculate_event_opportunities
from surebet.storage.auth import SessionData
from surebet.storage.settings import (
    SystemSettingsRepository,
    UserPreferences,
    UserPreferencesRepository,
)
from surebet.taxonomy.models import CompetitionKind, RegionClass, TaxonomyStatus
from surebet.taxonomy.normalizer import turkish_sort_key
from surebet.web.auth_middleware import require_authenticated_session

router = APIRouter(tags=["taxonomy"])

_MAX_LEAGUES_LIMIT = 200
_REGION_CLASS_RANK = {
    RegionClass.INTERNATIONAL: 0,
    RegionClass.COUNTRY: 1,
    RegionClass.UNKNOWN: 2,
}


class CountryItem(BaseModel):
    country_id: str
    region_class: RegionClass
    iso_code: str | None
    display_name: str
    league_count: int
    event_count: int
    opportunity_count: int
    freshest_at: datetime | None


class LeagueItem(BaseModel):
    league_id: str
    country_id: str
    display_name: str
    kind: CompetitionKind
    gender: str | None
    age_group: str | None
    is_reserve: bool | None
    active: bool
    event_count: int
    opportunity_count: int
    freshest_at: datetime | None


def _freshest_quote_at(quotes: Iterable[Quote]) -> datetime | None:
    timestamps = [quote.observed_at for quote in quotes]
    return max(timestamps) if timestamps else None


def _positive_opportunity_counts(
    matched_events: tuple[MatchedEvent, ...],
    preferences: UserPreferences,
    staleness_limit_seconds: int,
    now: datetime,
) -> dict[str, int]:
    """Count positive opportunities per league_id, from the same verified catalog
    used by /events and /opportunities. Unverified/unmapped events never enter here
    (calculate_event_opportunities short-circuits on non-VERIFIED taxonomy_status),
    so they cannot inflate any league's or country's opportunity_count.

    `preferences` (budget, Source A policy) is the calling session's own personal
    preferences; `staleness_limit_seconds` is the shared system value -- neither
    ever comes from another user's data."""
    counts: dict[str, int] = defaultdict(int)
    for event in matched_events:
        if event.taxonomy_status is not TaxonomyStatus.VERIFIED or event.league_id is None:
            continue
        opps = calculate_event_opportunities(
            event=event,
            budget_cents=preferences.default_budget_cents,
            include_source_a_match_sides=preferences.include_source_a_match_sides,
            now=now,
            stale_after_seconds=staleness_limit_seconds,
        )
        counts[event.league_id] += sum(1 for opp in opps if opp.is_positive)
    return counts


def _build_league_items(
    snapshot: CatalogSnapshot,
    preferences: UserPreferences,
    staleness_limit_seconds: int,
    now: datetime,
) -> list[LeagueItem]:
    events_by_league: dict[str, list[MatchedEvent]] = defaultdict(list)
    for event in snapshot.matched_events:
        if event.league_id is not None:
            events_by_league[event.league_id].append(event)

    opportunity_counts = _positive_opportunity_counts(
        snapshot.matched_events, preferences, staleness_limit_seconds, now
    )

    items: list[LeagueItem] = []
    for league in snapshot.leagues:
        league_events = events_by_league.get(league.league_id, ())
        quotes = (quote for event in league_events for quote in event.quotes.values())
        items.append(
            LeagueItem(
                league_id=league.league_id,
                country_id=league.region_id,
                display_name=league.display_name,
                kind=league.kind,
                gender=league.gender,
                age_group=league.age_group,
                is_reserve=league.is_reserve,
                active=league.active,
                event_count=len(league_events),
                opportunity_count=opportunity_counts.get(league.league_id, 0),
                freshest_at=_freshest_quote_at(quotes),
            )
        )
    items.sort(key=lambda item: (turkish_sort_key(item.display_name), item.league_id))
    return items


def _build_country_items(
    snapshot: CatalogSnapshot, league_items: list[LeagueItem]
) -> list[CountryItem]:
    leagues_by_region: dict[str, list[LeagueItem]] = defaultdict(list)
    for item in league_items:
        leagues_by_region[item.country_id].append(item)

    items: list[CountryItem] = []
    for region in snapshot.regions:
        region_leagues = leagues_by_region.get(region.region_id, [])
        if not region_leagues:
            continue
        freshest_candidates = [
            item.freshest_at for item in region_leagues if item.freshest_at is not None
        ]
        items.append(
            CountryItem(
                country_id=region.region_id,
                region_class=region.region_class,
                iso_code=region.iso_code,
                display_name=region.display_name,
                league_count=len(region_leagues),
                event_count=sum(item.event_count for item in region_leagues),
                opportunity_count=sum(item.opportunity_count for item in region_leagues),
                freshest_at=max(freshest_candidates) if freshest_candidates else None,
            )
        )
    items.sort(
        key=lambda item: (
            _REGION_CLASS_RANK[item.region_class],
            turkish_sort_key(item.display_name),
            item.country_id,
        )
    )
    return items


@router.get("/countries", response_model=list[CountryItem])
def list_countries(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    full_catalog: Annotated[bool, Query()] = False,
) -> list[CountryItem]:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    now = datetime.now(UTC)

    preferences = user_preferences_repo.get(session.user_id)
    staleness_limit_seconds = system_settings_repo.get().staleness_limit_seconds
    snapshot = catalog_service.build(
        now,
        # Scoring is locked to the tracked registry; `full_catalog` stays as the
        # mapping admin's deliberate override, since mapping an untracked
        # competition requires seeing it first.
        tracked_only=not full_catalog,
    )
    league_items = _build_league_items(snapshot, preferences, staleness_limit_seconds, now)
    return _build_country_items(snapshot, league_items)


@router.get("/leagues", response_model=list[LeagueItem])
def list_leagues(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    country_id: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    full_catalog: Annotated[bool, Query()] = False,
) -> list[LeagueItem]:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    now = datetime.now(UTC)

    preferences = user_preferences_repo.get(session.user_id)
    staleness_limit_seconds = system_settings_repo.get().staleness_limit_seconds
    snapshot = catalog_service.build(
        now,
        # Scoring is locked to the tracked registry; `full_catalog` stays as the
        # mapping admin's deliberate override, since mapping an untracked
        # competition requires seeing it first.
        tracked_only=not full_catalog,
    )
    league_items = _build_league_items(snapshot, preferences, staleness_limit_seconds, now)

    if country_id:
        wanted = set(country_id)
        league_items = [item for item in league_items if item.country_id in wanted]

    effective_limit = min(limit, _MAX_LEAGUES_LIMIT)
    return league_items[offset : offset + effective_limit]

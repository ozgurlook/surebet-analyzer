"""Opportunities listing and detail API."""

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from surebet.catalog.service import CatalogService
from surebet.domain.enums import Sport
from surebet.opportunities.strategy import CalculatedOpportunity, calculate_event_opportunities
from surebet.storage.auth import SessionData
from surebet.storage.settings import SystemSettingsRepository, UserPreferencesRepository
from surebet.storage.user_filters import FilterScope, UserFilterRepository
from surebet.web.auth_middleware import require_authenticated_session

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


def _wanted_ids(explicit: list[str] | None, saved: frozenset[str]) -> set[str] | None:
    """Resolve the effective ID set for one taxonomy dimension.

    `explicit` is the raw query parameter: `None` means the caller omitted
    it (use `saved`, the user's persisted filter for this scope); any list,
    including an empty one, is a request-local override applied verbatim.
    Either way, an empty resulting ID set means "no narrowing" (`None`), not
    "match nothing".
    """
    ids = explicit if explicit is not None else saved
    return set(ids) if ids else None


@router.get("", response_model=list[CalculatedOpportunity])
def list_opportunities(
    request: Request,
    response: Response,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    budget_cents: Annotated[int | None, Query(ge=1_000, le=1_000_000_000)] = None,
    include_source_a_match_sides: Annotated[bool | None, Query()] = None,
    positive_only: Annotated[bool, Query()] = True,
    country_id: Annotated[list[str] | None, Query()] = None,
    league_id: Annotated[list[str] | None, Query()] = None,
    sport: Annotated[Sport | None, Query()] = None,
    use_saved_filters: Annotated[bool, Query()] = True,
) -> list[CalculatedOpportunity]:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    user_filter_repo: UserFilterRepository = request.app.state.user_filter_repo

    preferences = user_preferences_repo.get(session.user_id)
    system_settings = system_settings_repo.get()
    budget = budget_cents if budget_cents is not None else preferences.default_budget_cents
    include_source_a = (
        include_source_a_match_sides
        if include_source_a_match_sides is not None
        else preferences.include_source_a_match_sides
    )

    now = datetime.now(UTC)

    # Scoring is locked to the curated tracked-competition registry: the product
    # only covers those leagues, so the catalog never widens.
    snapshot = catalog_service.build(now, tracked_only=True)
    opportunities: list[CalculatedOpportunity] = []

    # `country_id`/`league_id` being omitted (None) means "use my saved
    # filters for this scope"; an explicitly supplied list -- even an empty
    # one -- is a one-off request-local override that is applied as-is and
    # never written back to the saved filters.
    #
    # A plain query string cannot express "explicitly cleared" (an empty
    # list serializes to no param at all, indistinguishable from "omitted").
    # `use_saved_filters=False` closes that gap: the caller is asserting a
    # full explicit override for both dimensions, so an omitted param is
    # treated as an explicit empty list (no narrowing), never as "fall back
    # to saved".
    effective_country_id = country_id if use_saved_filters else (country_id or [])
    effective_league_id = league_id if use_saved_filters else (league_id or [])
    saved_filters = (
        user_filter_repo.get(session.user_id, FilterScope.OPPORTUNITIES)
        if use_saved_filters and (country_id is None or league_id is None)
        else None
    )
    wanted_countries = _wanted_ids(
        effective_country_id, saved_filters.country_ids if saved_filters else frozenset()
    )
    wanted_leagues = _wanted_ids(
        effective_league_id, saved_filters.league_ids if saved_filters else frozenset()
    )

    if snapshot.version_tag:
        c_part = sorted(effective_country_id) if effective_country_id is not None else ""
        l_part = sorted(effective_league_id) if effective_league_id is not None else ""
        raw_key = f"{snapshot.version_tag}:{session.user_id}:{budget}:{include_source_a}:{positive_only}:{c_part}:{l_part}:{sport.value if sport else ''}"
        etag_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        etag_val = f'W/"opp-{etag_hash}"'
        response.headers["ETag"] = etag_val
        client_etag = request.headers.get("if-none-match")
        if client_etag and client_etag == etag_val:
            return Response(status_code=304, headers={"ETag": etag_val})  # type: ignore[return-value]

    for ev in snapshot.matched_events:
        if wanted_countries is not None and ev.country_id not in wanted_countries:
            continue
        if wanted_leagues is not None and ev.league_id not in wanted_leagues:
            continue
        if sport is not None and ev.sport != sport:
            continue

        opps = calculate_event_opportunities(
            event=ev,
            budget_cents=budget,
            include_source_a_match_sides=include_source_a,
            now=now,
            stale_after_seconds=system_settings.staleness_limit_seconds,
        )
        if not opps:
            continue

        best = opps[0]
        if positive_only and not best.is_positive:
            continue

        alternatives = tuple(opps[1:])
        primary = best.model_copy(update={"alternatives": alternatives})
        opportunities.append(primary)

    opportunities.sort(key=lambda o: (-o.min_adjusted_profit_cents, o.kickoff, o.opportunity_id))
    return opportunities


@router.get("/{opportunity_id}", response_model=CalculatedOpportunity)
def get_opportunity_detail(
    opportunity_id: str,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    budget_cents: Annotated[int | None, Query(ge=1_000, le=1_000_000_000)] = None,
    include_source_a_match_sides: Annotated[bool | None, Query()] = None,
) -> CalculatedOpportunity:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo

    preferences = user_preferences_repo.get(session.user_id)
    system_settings = system_settings_repo.get()
    budget = budget_cents if budget_cents is not None else preferences.default_budget_cents
    include_source_a = (
        include_source_a_match_sides
        if include_source_a_match_sides is not None
        else preferences.include_source_a_match_sides
    )

    now = datetime.now(UTC)

    # Scoring is locked to the curated tracked-competition registry: the product
    # only covers those leagues, so the catalog never widens.
    snapshot = catalog_service.build(now, tracked_only=True)

    # An `opportunity_id` is "{matched_id}:{home}:{draw}:{away}", and a bare
    # `matched_id` is also accepted, so either form names its event up front --
    # no need to score the rest of the catalog looking for it.
    wanted_matched_id = opportunity_id.split(":", 1)[0]

    for ev in snapshot.matched_events:
        if ev.matched_id != wanted_matched_id:
            continue
        opps = calculate_event_opportunities(
            event=ev,
            budget_cents=budget,
            include_source_a_match_sides=include_source_a,
            now=now,
            stale_after_seconds=system_settings.staleness_limit_seconds,
        )
        for opp in opps:
            if opp.opportunity_id == opportunity_id or opp.matched_id == opportunity_id:
                alternatives = tuple(o for o in opps if o.opportunity_id != opp.opportunity_id)
                return opp.model_copy(update={"alternatives": alternatives})

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Fırsat bulunamadı",
    )

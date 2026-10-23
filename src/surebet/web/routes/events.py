"""All matched events API (including neutral and negative)."""

import hashlib
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from surebet.catalog.service import CatalogService
from surebet.domain.enums import Sport
from surebet.matching.models import MatchedEvent
from surebet.opportunities.strategy import calculate_event_opportunities
from surebet.storage.auth import SessionData
from surebet.storage.settings import SystemSettingsRepository, UserPreferencesRepository
from surebet.storage.user_filters import FilterScope, UserFilterRepository
from surebet.taxonomy.models import TaxonomyStatus
from surebet.taxonomy.normalizer import normalize_taxonomy_text
from surebet.web.auth_middleware import require_authenticated_session

router = APIRouter(prefix="/events", tags=["events"])

_ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


class EventListItem(BaseModel):
    matched_id: str
    taxonomy_status: TaxonomyStatus
    competition: str
    country_id: str | None = None
    country_name: str | None = None
    country_iso_code: str | None = None
    league_id: str | None = None
    league_name: str | None = None
    home: str
    away: str
    kickoff: datetime
    sources: list[str]
    best_margin_percent: float | None = None
    is_positive: bool = False
    sport: Sport = Sport.FOOTBALL
    quotes: dict[str, Any]


def _istanbul_scope_bounds(
    now: datetime, time_scope: Literal["today", "tomorrow", "all"]
) -> tuple[datetime, datetime] | None:
    """Half-open [start, end) Europe/Istanbul calendar-day bounds for `time_scope`,
    or None when no bound applies (`all`). Comparisons against these bounds must use
    the raw aware `kickoff` value -- aware datetimes compare by absolute instant, so
    there is no need to convert `kickoff` into Istanbul time first."""
    if time_scope == "all":
        return None
    now_istanbul = now.astimezone(_ISTANBUL_TZ)
    today_start = datetime.combine(now_istanbul.date(), time.min, tzinfo=_ISTANBUL_TZ)
    offset_days = 0 if time_scope == "today" else 1
    start = today_start + timedelta(days=offset_days)
    end = start + timedelta(days=1)
    return start, end


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


def _matches_search(ev: MatchedEvent, query: str) -> bool:
    normalized_query = normalize_taxonomy_text(query)
    if not normalized_query:
        return True
    haystacks = [ev.home, ev.away, ev.competition]
    if ev.country_name:
        haystacks.append(ev.country_name)
    if ev.league_name:
        haystacks.append(ev.league_name)
    return any(normalized_query in normalize_taxonomy_text(text) for text in haystacks)


@router.get("", response_model=list[EventListItem])
def list_events(
    request: Request,
    response: Response,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    search: Annotated[str | None, Query()] = None,
    time_scope: Annotated[Literal["today", "tomorrow", "all"], Query()] = "today",
    country_id: Annotated[list[str] | None, Query()] = None,
    league_id: Annotated[list[str] | None, Query()] = None,
    sport: Annotated[Sport | None, Query()] = None,
    use_saved_filters: Annotated[bool, Query()] = True,
) -> list[EventListItem]:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    user_filter_repo: UserFilterRepository = request.app.state.user_filter_repo

    preferences = user_preferences_repo.get(session.user_id)
    system_settings = system_settings_repo.get()
    now = datetime.now(UTC)

    # Scoring is locked to the curated tracked-competition registry: the product
    # only covers those leagues, so the catalog never widens.
    snapshot = catalog_service.build(now, tracked_only=True)
    results: list[EventListItem] = []

    scope_bounds = _istanbul_scope_bounds(now, time_scope)

    # `country_id`/`league_id` being omitted (None) means "use my saved
    # filters for this scope"; an explicitly supplied list -- even an empty
    # one -- is a one-off request-local override that is applied as-is and
    # never written back to the saved filters. An empty saved set (or an
    # explicit empty override) means no narrowing at all.
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
        user_filter_repo.get(session.user_id, FilterScope.EVENTS)
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
        raw_key = (
            f"{snapshot.version_tag}:{session.user_id}:{search}:{time_scope}:{c_part}:{l_part}"
        )
        etag_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        etag_val = f'W/"ev-{etag_hash}"'
        response.headers["ETag"] = etag_val
        client_etag = request.headers.get("if-none-match")
        if client_etag and client_etag == etag_val:
            return Response(status_code=304, headers={"ETag": etag_val})  # type: ignore[return-value]

    for ev in snapshot.matched_events:
        if scope_bounds is not None:
            scope_start, scope_end = scope_bounds
            if not (scope_start <= ev.kickoff < scope_end):
                continue
        if wanted_countries is not None and ev.country_id not in wanted_countries:
            continue
        if wanted_leagues is not None and ev.league_id not in wanted_leagues:
            continue
        if sport is not None and ev.sport != sport:
            continue
        if search and not _matches_search(ev, search):
            continue

        opps = calculate_event_opportunities(
            event=ev,
            budget_cents=preferences.default_budget_cents,
            include_source_a_match_sides=preferences.include_source_a_match_sides,
            now=now,
            stale_after_seconds=system_settings.staleness_limit_seconds,
        )

        best_opp = opps[0] if opps else None
        best_margin = best_opp.min_adjusted_profit_percent if best_opp else None
        is_pos = best_opp.is_positive if best_opp else False

        if ev.sport is Sport.BASKETBALL:
            quotes_dict = {
                f"{src.value}:{outcome.value}": quote.odds
                for (src, outcome), quote in ev.bask_ml_quotes.items()
            }
        else:
            quotes_dict = {
                f"{src.value}:{outcome.value}": quote.odds
                for (src, outcome), quote in ev.quotes.items()
            }

        results.append(
            EventListItem(
                matched_id=ev.matched_id,
                taxonomy_status=ev.taxonomy_status,
                competition=ev.competition,
                country_id=ev.country_id,
                country_name=ev.country_name,
                country_iso_code=ev.country_iso_code,
                league_id=ev.league_id,
                league_name=ev.league_name,
                home=ev.home,
                away=ev.away,
                kickoff=ev.kickoff,
                sources=[s.value for s in ev.source_events],
                best_margin_percent=best_margin,
                is_positive=is_pos,
                sport=ev.sport,
                quotes=quotes_dict,
            )
        )

    return results

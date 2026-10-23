"""Dashboard summary API."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from surebet.catalog.service import CatalogService
from surebet.domain.enums import Source
from surebet.opportunities.strategy import CalculatedOpportunity, calculate_event_opportunities
from surebet.storage.auth import SessionData
from surebet.storage.settings import SystemSettingsRepository, UserPreferencesRepository
from surebet.storage.snapshots import SourceHealthStatus
from surebet.web.auth_middleware import require_authenticated_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardResponse(BaseModel):
    total_opportunities_count: int
    positive_opportunities_count: int
    best_margin_percent: float
    sources_health: dict[str, Any]
    overall_status: str
    last_refresh_at: datetime | None


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
) -> DashboardResponse:
    catalog_service: CatalogService = request.app.state.catalog_service
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo

    preferences = user_preferences_repo.get(session.user_id)
    system_settings = system_settings_repo.get()
    now = datetime.now(UTC)

    # Scoring is locked to the curated tracked-competition registry: the product
    # only covers those leagues, so the catalog never widens.
    snapshot = catalog_service.build(now, tracked_only=True)

    opportunities: list[CalculatedOpportunity] = []
    for ev in snapshot.matched_events:
        opps = calculate_event_opportunities(
            event=ev,
            budget_cents=preferences.default_budget_cents,
            include_source_a_match_sides=preferences.include_source_a_match_sides,
            now=now,
            stale_after_seconds=system_settings.staleness_limit_seconds,
        )
        opportunities.extend(opps)

    positive_opps = [o for o in opportunities if o.is_positive]
    best_margin = max((o.min_adjusted_profit_percent for o in positive_opps), default=0.0)

    health_map = snapshot.source_health
    sources_health_dict = {
        src.value: health_map[src].model_dump(mode="json") for src in Source if src in health_map
    }

    # Determine overall status
    if not health_map:
        overall_status = "unavailable"
    elif all(h.status == SourceHealthStatus.HEALTHY for h in health_map.values()):
        overall_status = "live"
    elif any(h.status == SourceHealthStatus.HEALTHY for h in health_map.values()):
        overall_status = "degraded"
    else:
        overall_status = "unavailable"

    last_refresh = (
        max((h.finished_at for h in health_map.values()), default=None) if health_map else None
    )

    return DashboardResponse(
        total_opportunities_count=len(opportunities),
        positive_opportunities_count=len(positive_opps),
        best_margin_percent=best_margin,
        sources_health=sources_health_dict,
        overall_status=overall_status,
        last_refresh_at=last_refresh,
    )

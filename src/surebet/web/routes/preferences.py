"""Saved per-user taxonomy filter API.

`GET /preferences/filters/{scope}` returns the session user's saved country
and league IDs for one scope (`events` or `opportunities`). `PUT
/preferences/filters/{scope}` atomically replaces both sets for that scope --
ownership always comes from `session.user_id`, never from a client-supplied
field, matching the `settings` router's pattern. An unknown `scope` in the
URL is rejected by FastAPI's enum path-param validation before the handler
ever runs.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from surebet.storage.auth import SessionData
from surebet.storage.user_filters import (
    FilterScope,
    InvalidTaxonomyId,
    SavedTaxonomyFilters,
    UserFilterRepository,
)
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(prefix="/preferences", tags=["preferences"])


class SavedTaxonomyFiltersUpdate(BaseModel):
    country_ids: list[str] = []
    league_ids: list[str] = []


@router.get("/filters/{scope}", response_model=SavedTaxonomyFilters)
def get_filters(
    scope: FilterScope,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
) -> SavedTaxonomyFilters:
    user_filter_repo: UserFilterRepository = request.app.state.user_filter_repo
    return user_filter_repo.get(session.user_id, scope)


@router.put("/filters/{scope}", response_model=SavedTaxonomyFilters)
def put_filters(
    scope: FilterScope,
    updates: SavedTaxonomyFiltersUpdate,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> SavedTaxonomyFilters:
    user_filter_repo: UserFilterRepository = request.app.state.user_filter_repo
    try:
        return user_filter_repo.replace(
            session.user_id, scope, updates.country_ids, updates.league_ids
        )
    except InvalidTaxonomyId as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

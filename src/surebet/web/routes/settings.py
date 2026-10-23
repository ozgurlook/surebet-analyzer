"""Personal preference and shared system settings API.

`GET /settings` returns both sections for the caller's convenience. Writes are
split into two independent endpoints matching the two independent
repositories: `PUT /settings/personal` only ever touches the session user's
own `user_preferences` row (ownership always comes from `session.user_id`,
never from a client-supplied field), and `PUT /settings/system` touches the
shared `system_settings` singleton, visible to and writable by every
authenticated user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from surebet.storage.auth import SessionData
from surebet.storage.settings import (
    SystemSettings,
    SystemSettingsRepository,
    SystemSettingsUpdate,
    UserPreferences,
    UserPreferencesRepository,
    UserPreferencesUpdate,
)
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    personal: UserPreferences
    system: SystemSettings


@router.get("", response_model=SettingsResponse)
def get_settings(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
) -> SettingsResponse:
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    return SettingsResponse(
        personal=user_preferences_repo.get(session.user_id),
        system=system_settings_repo.get(),
    )


@router.put("/personal", response_model=UserPreferences)
def update_personal_settings(
    updates: UserPreferencesUpdate,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> UserPreferences:
    user_preferences_repo: UserPreferencesRepository = request.app.state.user_preferences_repo
    return user_preferences_repo.update(session.user_id, updates)


@router.put("/system", response_model=SystemSettings)
def update_system_settings(
    updates: SystemSettingsUpdate,
    request: Request,
    _session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> SystemSettings:
    system_settings_repo: SystemSettingsRepository = request.app.state.system_settings_repo
    return system_settings_repo.update(updates)

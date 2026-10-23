"""Authenticated, token-free Telegram notification settings and connection test.

Ownership of every settings row is `session.user_id` -- the session never
accepts a client-supplied user ID in the request body or URL. See
`storage/notifications.py`'s module docstring for why `chat_id` is never used
as a lookup key.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from surebet.notifications.telegram import TelegramClient
from surebet.storage.auth import SessionData
from surebet.storage.notifications import (
    NotificationRepository,
    TelegramNotificationSettings,
    TelegramSettingsUpdate,
)
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(prefix="/notifications", tags=["notifications"])

_TEST_MESSAGE = "✅ Surebet Analyzer Telegram bağlantısı başarılı."
_MAX_CHAT_ID = 9_223_372_036_854_775_807
_TOKEN_MISSING_DETAIL = "Telegram bot tokeni yapılandırılmamış"
_CHAT_NOT_VALIDATED_DETAIL = "Chat kimliği doğrulanmadı"
_CHAT_ID_NOT_SET_DETAIL = "Telegram chat ID ayarlanmamış"


def percent_to_bps(value: Decimal) -> int:
    if not value.is_finite() or value < Decimal("0.01") or value > Decimal("100.00"):
        raise ValueError("percentage must be between 0.01 and 100.00")
    scaled = value * 100
    if scaled != scaled.to_integral_value():
        raise ValueError("percentage supports at most two decimal places")
    return int(scaled)


def bps_to_percent(bps: int) -> str:
    return str((Decimal(bps) / 100).quantize(Decimal("0.01")))


def parse_private_chat_id(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal():
        raise ValueError("chat id must be a positive integer")
    parsed = int(value)
    if parsed < 1 or parsed > _MAX_CHAT_ID:
        raise ValueError("chat id is outside the supported range")
    return parsed


class TelegramNotificationUpdateRequest(BaseModel):
    enabled: bool | None = None
    chat_id: str | None = None
    minimum_profit_percent: str | None = None
    decrease_threshold_percentage_points: str | None = None
    increase_threshold_percentage_points: str | None = None
    alert_cooldown_seconds: int | None = Field(default=None, ge=60, le=86_400)
    notify_1x2: bool | None = None
    notify_ou25: bool | None = None


class TelegramNotificationConfigResponse(BaseModel):
    enabled: bool
    chat_id: str | None
    minimum_profit_percent: str
    decrease_threshold_percentage_points: str
    increase_threshold_percentage_points: str
    alert_cooldown_seconds: int
    token_configured: bool
    chat_validated: bool
    validated_at: datetime | None
    last_successful_delivery_at: datetime | None
    last_error: str | None
    notify_1x2: bool
    notify_ou25: bool


def _token_configured(request: Request) -> bool:
    return bool(request.app.state.telegram_token_configured)


def _repo(request: Request) -> NotificationRepository:
    return request.app.state.notification_repo  # type: ignore[no-any-return]


def _to_response(
    settings: TelegramNotificationSettings, token_configured: bool
) -> TelegramNotificationConfigResponse:
    return TelegramNotificationConfigResponse(
        enabled=settings.enabled,
        chat_id=str(settings.chat_id) if settings.chat_id is not None else None,
        minimum_profit_percent=bps_to_percent(settings.minimum_profit_bps),
        decrease_threshold_percentage_points=bps_to_percent(settings.decrease_threshold_bps),
        increase_threshold_percentage_points=bps_to_percent(settings.increase_threshold_bps),
        alert_cooldown_seconds=settings.alert_cooldown_seconds,
        token_configured=token_configured,
        chat_validated=(
            settings.chat_id is not None and settings.chat_id == settings.validated_chat_id
        ),
        validated_at=settings.validated_at,
        last_successful_delivery_at=settings.last_successful_delivery_at,
        last_error=settings.last_error,
        notify_1x2=settings.notify_1x2,
        notify_ou25=settings.notify_ou25,
    )


@router.get("/telegram", response_model=TelegramNotificationConfigResponse)
def get_telegram_config(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
) -> TelegramNotificationConfigResponse:
    return _to_response(_repo(request).get_settings(session.user_id), _token_configured(request))


@router.put("/telegram", response_model=TelegramNotificationConfigResponse)
def update_telegram_config(
    payload: TelegramNotificationUpdateRequest,
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> TelegramNotificationConfigResponse:
    repo = _repo(request)
    token_configured = _token_configured(request)
    fields = payload.model_fields_set

    kwargs: dict[str, object] = {}
    try:
        if "enabled" in fields:
            kwargs["enabled"] = payload.enabled
        if "chat_id" in fields:
            kwargs["chat_id"] = parse_private_chat_id(payload.chat_id)
        if "minimum_profit_percent" in fields:
            assert payload.minimum_profit_percent is not None
            kwargs["minimum_profit_bps"] = percent_to_bps(Decimal(payload.minimum_profit_percent))
        if "decrease_threshold_percentage_points" in fields:
            assert payload.decrease_threshold_percentage_points is not None
            kwargs["decrease_threshold_bps"] = percent_to_bps(
                Decimal(payload.decrease_threshold_percentage_points)
            )
        if "increase_threshold_percentage_points" in fields:
            assert payload.increase_threshold_percentage_points is not None
            kwargs["increase_threshold_bps"] = percent_to_bps(
                Decimal(payload.increase_threshold_percentage_points)
            )
        if "alert_cooldown_seconds" in fields:
            kwargs["alert_cooldown_seconds"] = payload.alert_cooldown_seconds
        if "notify_1x2" in fields:
            kwargs["notify_1x2"] = payload.notify_1x2
        if "notify_ou25" in fields:
            kwargs["notify_ou25"] = payload.notify_ou25
    except (ValueError, InvalidOperation, AssertionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    current = repo.get_settings(session.user_id)
    if kwargs.get("enabled") is True:
        resulting_chat_id = kwargs.get("chat_id", current.chat_id)
        if not token_configured:
            raise HTTPException(status_code=409, detail=_TOKEN_MISSING_DETAIL)
        if resulting_chat_id is None or resulting_chat_id != current.validated_chat_id:
            raise HTTPException(status_code=409, detail=_CHAT_NOT_VALIDATED_DETAIL)

    updated = repo.update_settings(session.user_id, TelegramSettingsUpdate.model_validate(kwargs))
    return _to_response(updated, token_configured)


@router.post("/telegram/test", response_model=TelegramNotificationConfigResponse)
async def test_telegram_connection(
    request: Request,
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> TelegramNotificationConfigResponse:
    repo = _repo(request)
    token_configured = _token_configured(request)
    if not token_configured:
        raise HTTPException(status_code=409, detail=_TOKEN_MISSING_DETAIL)

    settings = repo.get_settings(session.user_id)
    if settings.chat_id is None:
        raise HTTPException(status_code=409, detail=_CHAT_ID_NOT_SET_DETAIL)

    client: TelegramClient = request.app.state.telegram_client
    result = await client.test_chat(chat_id=settings.chat_id, text=_TEST_MESSAGE)
    if not result.ok:
        error = result.safe_error or "Telegram test failed"
        repo.record_delivery_error(session.user_id, error)
        raise HTTPException(status_code=502, detail=error)

    now = datetime.now(UTC)
    repo.mark_chat_validated(session.user_id, settings.chat_id, now)
    repo.record_delivery_success(session.user_id, now)
    return _to_response(repo.get_settings(session.user_id), token_configured)

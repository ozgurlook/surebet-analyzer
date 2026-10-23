"""Authentication routes: registration, username/password login, logout, and
session state.

Registration is only enabled when the server was started with
`SUREBET_REGISTRATION_CODE` configured (see `surebet.web.app.create_app`).
The raw code is never stored on `app.state`; only a SHA-256 digest of it
(needed for the timing-safe comparison below) and a `registration_enabled`
boolean are kept. Neither the code, its digest, nor a submitted password ever
appears in a response body.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from surebet.storage.auth import (
    SESSION_DURATION,
    AuthRepository,
    DuplicateUsernameError,
    InvalidPasswordError,
    InvalidUsernameError,
    SessionData,
)
from surebet.storage.auth_rate_limit import AuthAction, AuthRateLimitRepository
from surebet.web.auth_middleware import (
    get_auth_repo,
    get_current_session,
    require_authenticated_session,
    require_csrf_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_NAME = "session_token"
_COOKIE_MAX_AGE_SECONDS = int(SESSION_DURATION.total_seconds())

_REGISTRATION_DISABLED_DETAIL = "Yeni kullanıcı kaydı kapalı"
_REGISTRATION_INVALID_DETAIL = "Kayıt bilgileri geçersiz"
_DUPLICATE_USERNAME_DETAIL = "Bu kullanıcı adı kullanılıyor"
_LOGIN_INVALID_DETAIL = "Geçersiz kullanıcı adı veya parola"
_RATE_LIMITED_DETAIL = "Çok fazla deneme, daha sonra tekrar deneyin"


class RegisterRequest(BaseModel):
    username: str
    password: str
    registration_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserResponse(BaseModel):
    """Returned by a successful registration or login."""

    user_id: int
    username: str
    csrf_token: str


class AuthStateResponse(BaseModel):
    authenticated: bool
    registration_enabled: bool
    user_id: int | None = None
    username: str | None = None
    csrf_token: str | None = None


class LogoutResponse(BaseModel):
    status: str


def _rate_limit_repo(request: Request) -> AuthRateLimitRepository:
    return request.app.state.auth_rate_limit_repo  # type: ignore[no-any-return]


def _client_ip(request: Request) -> str:
    # Deliberately request.client.host only -- never a forwarded header such
    # as X-Forwarded-For, which an untrusted client can set arbitrarily. If a
    # reverse proxy is ever introduced in front of this app, that proxy's
    # trusted address handling must be added here explicitly; do not widen
    # this to trust forwarded headers by default.
    if request.client is None:
        return "unknown"
    return request.client.host


def _registration_code_valid(submitted: str, request: Request) -> bool:
    digest: bytes | None = request.app.state.registration_code_digest
    if digest is None:
        return False
    submitted_digest = hashlib.sha256(submitted.encode("utf-8")).digest()
    return hmac.compare_digest(submitted_digest, digest)


def _raise_rate_limited(retry_after_seconds: int) -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=_RATE_LIMITED_DETAIL,
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _set_session_cookie(response: Response, request: Request, session: SessionData) -> None:
    secure = bool(request.app.state.cookie_secure)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session.token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


@router.get("/me", response_model=AuthStateResponse)
def get_auth_state(
    request: Request,
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> AuthStateResponse:
    registration_enabled = bool(request.app.state.registration_enabled)
    if session is None:
        return AuthStateResponse(authenticated=False, registration_enabled=registration_enabled)
    return AuthStateResponse(
        authenticated=True,
        registration_enabled=registration_enabled,
        user_id=session.user_id,
        username=session.username,
        csrf_token=session.csrf_token,
    )


@router.post("/register", response_model=AuthUserResponse)
def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    auth_repo: Annotated[AuthRepository, Depends(get_auth_repo)],
    rate_limit_repo: Annotated[AuthRateLimitRepository, Depends(_rate_limit_repo)],
) -> AuthUserResponse:
    client_ip = _client_ip(request)
    now = datetime.now(UTC)

    decision = rate_limit_repo.check(client_ip, AuthAction.REGISTER, now)
    if not decision.allowed:
        _raise_rate_limited(decision.retry_after_seconds)

    if not bool(request.app.state.registration_enabled):
        # Registration being closed entirely is not a credential-guessing
        # surface, so it does not count as a failed attempt.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_REGISTRATION_DISABLED_DETAIL,
        )

    if not _registration_code_valid(req.registration_code, request):
        rate_limit_repo.record_failure(client_ip, AuthAction.REGISTER, now)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_REGISTRATION_INVALID_DETAIL,
        )

    try:
        user, session = auth_repo.register_user(req.username, req.password, now=now)
    except (InvalidUsernameError, InvalidPasswordError) as exc:
        rate_limit_repo.record_failure(client_ip, AuthAction.REGISTER, now)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_REGISTRATION_INVALID_DETAIL,
        ) from exc
    except DuplicateUsernameError as exc:
        rate_limit_repo.record_failure(client_ip, AuthAction.REGISTER, now)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_DUPLICATE_USERNAME_DETAIL,
        ) from exc

    rate_limit_repo.clear(client_ip, AuthAction.REGISTER)
    _set_session_cookie(response, request, session)
    return AuthUserResponse(
        user_id=user.user_id, username=user.username, csrf_token=session.csrf_token
    )


@router.post("/login", response_model=AuthUserResponse)
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    auth_repo: Annotated[AuthRepository, Depends(get_auth_repo)],
    rate_limit_repo: Annotated[AuthRateLimitRepository, Depends(_rate_limit_repo)],
) -> AuthUserResponse:
    client_ip = _client_ip(request)
    now = datetime.now(UTC)

    decision = rate_limit_repo.check(client_ip, AuthAction.LOGIN, now)
    if not decision.allowed:
        _raise_rate_limited(decision.retry_after_seconds)

    user = auth_repo.verify_credentials(req.username, req.password)
    if user is None:
        # Covers unknown username, wrong password, and inactive accounts
        # identically -- never disclose which one occurred.
        rate_limit_repo.record_failure(client_ip, AuthAction.LOGIN, now)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_INVALID_DETAIL,
        )

    session = auth_repo.create_session(user.user_id, now=now)
    rate_limit_repo.clear(client_ip, AuthAction.LOGIN)
    _set_session_cookie(response, request, session)
    return AuthUserResponse(
        user_id=user.user_id, username=user.username, csrf_token=session.csrf_token
    )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    request: Request,
    auth_repo: Annotated[AuthRepository, Depends(get_auth_repo)],
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    _csrf: None = Depends(require_csrf_token),
) -> LogoutResponse:
    # Deletes only this one session (by its own token) -- other sessions for
    # the same user, on other devices, are left intact.
    auth_repo.delete_session(session.token)
    secure = bool(request.app.state.cookie_secure)
    response.delete_cookie(
        key=_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return LogoutResponse(status="logged_out")

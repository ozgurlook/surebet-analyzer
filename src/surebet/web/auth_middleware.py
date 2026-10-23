"""Authentication and CSRF dependencies for FastAPI.

`require_authenticated_session` only accepts a real, unexpired session that
belongs to an active user. There is no passwordless/open-access fallback: an
empty database (no users registered yet) is always unauthenticated.
"""

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from surebet.storage.auth import AuthRepository, SessionData

_UNAUTHENTICATED_DETAIL = "Authentication required"
_INVALID_CSRF_DETAIL = "Invalid or missing CSRF token"


def get_auth_repo(request: Request) -> AuthRepository:
    return request.app.state.auth_repo  # type: ignore[no-any-return]


def get_current_session(
    request: Request,
    auth_repo: Annotated[AuthRepository, Depends(get_auth_repo)],
    session_token: Annotated[str | None, Cookie()] = None,
) -> SessionData | None:
    if not session_token:
        return None
    return auth_repo.validate_session(session_token)


def require_authenticated_session(
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> SessionData:
    """Return the caller's real session, or raise 401.

    Missing, expired, or inactive-user sessions all return the same generic
    401 -- no personal response data is ever leaked here, and no "open
    access" pseudo-session is ever granted just because no user exists yet.
    """
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_DETAIL,
        )
    return session


def require_csrf_token(
    session: Annotated[SessionData, Depends(require_authenticated_session)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if not x_csrf_token or x_csrf_token != session.csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_INVALID_CSRF_DETAIL,
        )

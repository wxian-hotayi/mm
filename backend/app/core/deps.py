"""FastAPI dependencies: database session, current user and admin guard.

Authentication is **dual-mode** (DESIGN §20.8, Decision Log 26): the JWT access
token is read from the ``wos_access`` HttpOnly cookie first, falling back to an
``Authorization: Bearer <jwt>`` header. The cookie is preferred so browsers use
the hardened cookie session while existing Phase 1–2 bearer clients and tests
keep working unchanged. The decoded token resolves the active
:class:`~app.models.user.User`. Any failure — no credential, malformed/expired
token, unknown or deactivated user — raises
:class:`~app.core.errors.AuthError` (uniform 401).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, ForbiddenError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole

DbDep = Annotated[AsyncSession, Depends(get_db)]

#: Name of the HttpOnly cookie carrying the JWT access token (DESIGN §9).
ACCESS_COOKIE_NAME = "wos_access"

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token issued by POST /api/v1/auth/login",
)

BearerDep = Annotated[
    HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
]

AccessCookieDep = Annotated[
    str | None, Cookie(alias=ACCESS_COOKIE_NAME)
]


def _extract_token(
    cookie_token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    """Pick the access JWT: cookie first, then bearer header (DESIGN §20.8)."""
    if cookie_token:
        return cookie_token
    if credentials is not None and credentials.credentials:
        return credentials.credentials
    raise AuthError("Missing access token")


async def get_current_user(
    db: DbDep,
    credentials: BearerDep,
    access_cookie: AccessCookieDep = None,
) -> User:
    """Resolve the authenticated, active user from cookie-or-bearer JWT.

    Raises :class:`AuthError` (401) when no credential is presented, the token
    is invalid/expired, the subject claim is not a user id, or the user does
    not exist or is inactive.
    """
    token = _extract_token(access_cookie, credentials)
    payload = decode_access_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Invalid access token subject") from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("User account is unknown or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    """Allow only users with the admin role; raise ForbiddenError otherwise."""
    if user.role != UserRole.ADMIN.value:
        raise ForbiddenError("Administrator privileges required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]

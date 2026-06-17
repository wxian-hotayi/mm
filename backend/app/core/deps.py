"""FastAPI dependencies: database session, current user and admin guard.

Authentication is JWT bearer (Phase 1): the ``Authorization: Bearer <jwt>``
header is decoded and the active :class:`~app.models.user.User` is loaded
from the database. Any failure — missing header, malformed/expired token,
unknown or deactivated user — raises :class:`~app.core.errors.AuthError`
(uniform 401).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, ForbiddenError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole

DbDep = Annotated[AsyncSession, Depends(get_db)]

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token issued by POST /api/v1/auth/login",
)

BearerDep = Annotated[
    HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
]


async def get_current_user(db: DbDep, credentials: BearerDep) -> User:
    """Resolve the authenticated, active user from the bearer token.

    Raises :class:`AuthError` (401) when the Authorization header is missing
    or malformed, the token is invalid/expired, the subject claim is not a
    user id, or the user does not exist or is inactive.
    """
    if credentials is None:
        raise AuthError("Missing bearer token")
    payload = decode_access_token(credentials.credentials)
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

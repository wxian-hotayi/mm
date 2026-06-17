"""Authentication endpoints (DESIGN §9 / §20.8 end-state).

Cookie-based sessions are primary: ``POST /auth/login`` sets an HttpOnly
``wos_access`` cookie (the JWT) and an HttpOnly ``wos_refresh`` cookie (the
opaque rotating refresh secret, scoped to ``/api/v1/auth``), and **also**
returns the Phase-1 ``{token, user}`` body so existing bearer clients and tests
keep working (Decision Log 26). ``/auth/refresh`` rotates the session,
``/auth/logout`` revokes it, and the password-reset pair issues/consumes a
short-lived token (logged server-side; SMTP later).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.core.config import get_settings
from app.core.deps import ACCESS_COOKIE_NAME, CurrentUser, DbDep
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.services import auth_service
from app.schemas.auth import (
    LoginIn,
    LoginOut,
    OkOut,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    TokenOut,
    UserOut,
)

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

#: Refresh cookie name and the path it is scoped to (DESIGN §9).
_REFRESH_COOKIE_NAME = "wos_refresh"
_REFRESH_COOKIE_PATH = "/api/v1/auth"
_SAMESITE = "lax"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:512] if agent else None


def _set_access_cookie(response: Response, access_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=settings.ACCESS_TOKEN_MINUTES * 60,
        httponly=True,
        secure=settings.is_production,
        samesite=_SAMESITE,
        path="/",
    )


def _set_refresh_cookie(
    response: Response, raw_refresh: str, *, remember: bool
) -> None:
    settings = get_settings()
    days = (
        settings.REFRESH_DAYS_REMEMBER
        if remember
        else settings.REFRESH_DAYS_DEFAULT
    )
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=raw_refresh,
        max_age=days * 24 * 60 * 60,
        httponly=True,
        secure=settings.is_production,
        samesite=_SAMESITE,
        path=_REFRESH_COOKIE_PATH,
    )


def _set_session_cookies(
    response: Response,
    *,
    access_token: str,
    raw_refresh: str,
    remember: bool,
) -> None:
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, raw_refresh, remember=remember)


def _clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.is_production,
        samesite=_SAMESITE,
    )
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.is_production,
        samesite=_SAMESITE,
    )


def _login_body(result: auth_service.LoginResult) -> LoginOut:
    settings = get_settings()
    return LoginOut(
        token=TokenOut(
            access_token=result.access_token,
            expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        ),
        user=UserOut.model_validate(result.user),
    )


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginIn, request: Request, response: Response, db: DbDep
) -> LoginOut:
    """Authenticate, open a rotating session, set cookies, return the body."""
    result = await auth_service.login(
        db,
        identifier=payload.identifier,
        password=payload.password,
        remember=payload.remember,
        user_agent=_user_agent(request),
        ip=_client_ip(request),
    )
    _set_session_cookies(
        response,
        access_token=result.access_token,
        raw_refresh=result.raw_refresh,
        remember=result.remember,
    )
    return _login_body(result)


@router.post("/refresh", response_model=LoginOut)
async def refresh(
    request: Request, response: Response, db: DbDep
) -> LoginOut:
    """Rotate the refresh session from the ``wos_refresh`` cookie."""
    raw_refresh = request.cookies.get(_REFRESH_COOKIE_NAME)
    if not raw_refresh:
        raise AuthError("Missing refresh token")
    result = await auth_service.refresh(
        db,
        raw_refresh=raw_refresh,
        user_agent=_user_agent(request),
        ip=_client_ip(request),
    )
    _set_session_cookies(
        response,
        access_token=result.access_token,
        raw_refresh=result.raw_refresh,
        remember=result.remember,
    )
    return _login_body(result)


@router.post("/logout", response_model=OkOut)
async def logout(
    request: Request, response: Response, db: DbDep
) -> OkOut:
    """Revoke the current refresh session and clear both auth cookies."""
    raw_refresh = request.cookies.get(_REFRESH_COOKIE_NAME)
    await auth_service.logout(db, raw_refresh=raw_refresh)
    _clear_session_cookies(response)
    return OkOut()


@router.post("/password-reset/request", response_model=OkOut)
async def password_reset_request(
    payload: PasswordResetRequestIn, request: Request, db: DbDep
) -> OkOut:
    """Issue a reset token if the email is registered (no enumeration)."""
    await auth_service.request_password_reset(
        db, email=payload.email, ip=_client_ip(request)
    )
    return OkOut()


@router.post("/password-reset/confirm", response_model=OkOut)
async def password_reset_confirm(
    payload: PasswordResetConfirmIn, request: Request, db: DbDep
) -> OkOut:
    """Consume a reset token and set the new password (revokes sessions)."""
    await auth_service.confirm_password_reset(
        db,
        raw_token=payload.token,
        new_password=payload.new_password,
        ip=_client_ip(request),
    )
    return OkOut()


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile."""
    return UserOut.model_validate(user)

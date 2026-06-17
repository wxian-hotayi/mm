"""Authentication service: login, refresh rotation, logout and reset flows.

Implements the DESIGN §9 end-state on top of the Phase-1 JWT primitives: a
DB-backed rotating refresh session per login (``auth_sessions``), single-use
password-reset tokens (``password_reset_tokens``), and auditing of every AUTH
event. The HTTP layer (``app.api.v1.auth``) is thin — it parses the request,
calls these functions, and translates the returned secrets into HttpOnly
cookies plus the Phase-1 response body.

All money/credential secrets are stored only as SHA-256 hashes; the raw
refresh/reset tokens are returned to the caller exactly once.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    generate_reset_token,
    hash_password,
    hash_token,
    verify_dummy_password,
    verify_password,
)
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.auth_session import AuthSession, PasswordResetToken
from app.models.user import User

logger = get_logger("auth_service")

# Password-reset tokens are short-lived by design (DESIGN §9).
_RESET_TOKEN_TTL = timedelta(minutes=15)
# Password policy mirrors the admin-bootstrap expectations (non-trivial).
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 256


@dataclass(frozen=True)
class LoginResult:
    """The product of a successful login or refresh rotation.

    ``raw_refresh`` is the opaque secret the caller must place in the
    ``wos_refresh`` cookie; it is never persisted in plaintext.
    """

    user: User
    access_token: str
    raw_refresh: str
    remember: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime read back from the DB to tz-aware UTC.

    SQLite (via aiosqlite) returns naive datetimes even for ``DateTime(
    timezone=True)`` columns; treat such values as the UTC instants they were
    written as so they compare cleanly against :func:`_now`.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _refresh_lifetime(remember: bool) -> timedelta:
    settings = get_settings()
    days = (
        settings.REFRESH_DAYS_REMEMBER
        if remember
        else settings.REFRESH_DAYS_DEFAULT
    )
    return timedelta(days=days)


def _audit(
    *,
    user_id: int | None,
    action: str,
    description: str,
    severity: str = AuditSeverity.INFO.value,
    context: dict[str, object] | None = None,
    ip: str | None = None,
) -> AuditLog:
    return AuditLog(
        user_id=user_id,
        event_type=AuditEventType.AUTH.value,
        action=action,
        severity=severity,
        entity="user",
        entity_id=str(user_id) if user_id is not None else None,
        description=description,
        context=json.dumps(context or {}, default=str),
        ip=ip,
    )


async def _create_session(
    db: AsyncSession,
    *,
    user: User,
    remember: bool,
    user_agent: str | None,
    ip: str | None,
) -> str:
    """Create an AuthSession and return the raw (un-hashed) refresh secret."""
    raw_refresh, refresh_hash = generate_refresh_token()
    session = AuthSession(
        id=uuid.uuid4().hex,
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        user_agent=user_agent,
        ip=ip,
        remember=remember,
        expires_at=_now() + _refresh_lifetime(remember),
    )
    db.add(session)
    return raw_refresh


async def login(
    db: AsyncSession,
    *,
    identifier: str,
    password: str,
    remember: bool,
    user_agent: str | None = None,
    ip: str | None = None,
) -> LoginResult:
    """Authenticate by email or username and open a rotating refresh session.

    Raises :class:`AuthError` (401) on unknown/inactive accounts or a wrong
    password. The no-user / inactive-user branch still spends bcrypt time to
    defeat the username-enumeration timing oracle (Phase-1 invariant), and
    every failure is audited. On success the user's ``last_login_at`` is
    updated and a new ``auth_sessions`` row is created.
    """
    identifier = identifier.strip()
    result = await db.execute(
        select(User).where(
            or_(
                func.lower(User.email) == identifier.lower(),
                User.username == identifier,
            )
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        verify_dummy_password(password)
        password_ok = False
    else:
        password_ok = verify_password(password, user.password_hash)

    if not password_ok:
        logger.warning("Login failed", extra={"identifier": identifier})
        db.add(
            _audit(
                user_id=user.id if user is not None else None,
                action="LOGIN_FAILED",
                description="Failed login attempt",
                severity=AuditSeverity.WARNING.value,
                context={"identifier": identifier},
                ip=ip,
            )
        )
        await db.commit()
        raise AuthError("Invalid credentials")

    assert user is not None  # narrowed by password_ok being True
    user.last_login_at = _now()
    raw_refresh = await _create_session(
        db, user=user, remember=remember, user_agent=user_agent, ip=ip
    )
    db.add(
        _audit(
            user_id=user.id,
            action="LOGIN",
            description=f"User {user.username} logged in",
            ip=ip,
        )
    )
    await db.commit()
    return LoginResult(
        user=user,
        access_token=create_access_token(user.id, user.role),
        raw_refresh=raw_refresh,
        remember=remember,
    )


async def _active_session(
    db: AsyncSession, raw_refresh: str
) -> AuthSession:
    """Resolve a non-revoked, unexpired session for a raw refresh secret."""
    token_hash = hash_token(raw_refresh)
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == token_hash
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise AuthError("Invalid refresh token")
    if session.revoked_at is not None:
        raise AuthError("Refresh token has been revoked")
    if _as_utc(session.expires_at) <= _now():
        raise AuthError("Refresh token has expired")
    return session


async def refresh(
    db: AsyncSession,
    *,
    raw_refresh: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> LoginResult:
    """Rotate a refresh session: revoke the presented one, issue a new pair.

    Raises :class:`AuthError` (401) when the token is unknown, revoked or
    expired, or when the owning account is gone/inactive. Rotation keeps the
    original ``remember`` lifetime semantics.
    """
    session = await _active_session(db, raw_refresh)
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        # Revoke the orphaned session so a stolen token cannot be reused.
        session.revoked_at = _now()
        await db.commit()
        raise AuthError("User account is unknown or inactive")

    session.revoked_at = _now()
    raw_new_refresh = await _create_session(
        db,
        user=user,
        remember=session.remember,
        user_agent=user_agent,
        ip=ip,
    )
    db.add(
        _audit(
            user_id=user.id,
            action="REFRESH",
            description="Refresh token rotated",
            context={"session_id": session.id},
            ip=ip,
        )
    )
    await db.commit()
    return LoginResult(
        user=user,
        access_token=create_access_token(user.id, user.role),
        raw_refresh=raw_new_refresh,
        remember=session.remember,
    )


async def logout(db: AsyncSession, *, raw_refresh: str | None) -> None:
    """Revoke the session for a refresh secret (idempotent, never raises).

    Logout is best-effort: a missing or already-invalid cookie still clears
    the client side, so an unknown/revoked token is treated as success.
    """
    if not raw_refresh:
        return
    token_hash = hash_token(raw_refresh)
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == token_hash
        )
    )
    session = result.scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return
    session.revoked_at = _now()
    db.add(
        _audit(
            user_id=session.user_id,
            action="LOGOUT",
            description="Session revoked via logout",
            context={"session_id": session.id},
        )
    )
    await db.commit()


async def request_password_reset(
    db: AsyncSession, *, email: str, ip: str | None = None
) -> None:
    """Create a 15-minute reset token for ``email`` if the account exists.

    Returns nothing and never reveals whether the address is registered (no
    user enumeration, DESIGN §9). When a matching active user exists the raw
    token is logged server-side (``logger.warning``) — SMTP delivery is a
    roadmap item — and stored only as a SHA-256 hash.
    """
    email = email.strip()
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.lower())
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        logger.info(
            "Password-reset requested for unknown/inactive address",
            extra={"email": email},
        )
        return
    raw_token, token_hash = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_now() + _RESET_TOKEN_TTL,
        )
    )
    db.add(
        _audit(
            user_id=user.id,
            action="PASSWORD_RESET_REQUEST",
            description="Password reset token issued",
            ip=ip,
        )
    )
    await db.commit()
    # The raw token is intentionally surfaced only in the server log until
    # SMTP delivery is implemented (DESIGN §9, ROADMAP).
    logger.warning(
        "Password reset token issued (deliver via email in production)",
        extra={"user_id": user.id, "reset_token": raw_token},
    )


async def confirm_password_reset(
    db: AsyncSession,
    *,
    raw_token: str,
    new_password: str,
    ip: str | None = None,
) -> None:
    """Consume a reset token and set a new password (DESIGN §9).

    Raises :class:`AuthError` (401) when the token is unknown, already used or
    expired, or the owning account is gone/inactive. On success the token is
    stamped ``used_at`` (single-use), the password hash is replaced, and every
    other live refresh session for the user is revoked so a compromised
    session cannot survive a reset.
    """
    if not (
        _MIN_PASSWORD_LENGTH <= len(new_password) <= _MAX_PASSWORD_LENGTH
    ):
        raise AuthError(
            f"Password must be between {_MIN_PASSWORD_LENGTH} and "
            f"{_MAX_PASSWORD_LENGTH} characters"
        )
    token_hash = hash_token(raw_token)
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash
        )
    )
    token = result.scalar_one_or_none()
    if token is None or token.used_at is not None:
        raise AuthError("Invalid or already-used reset token")
    if _as_utc(token.expires_at) <= _now():
        raise AuthError("Reset token has expired")
    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthError("User account is unknown or inactive")

    now = _now()
    token.used_at = now
    user.password_hash = hash_password(new_password)
    # Revoke all live sessions so the reset invalidates prior logins.
    live_sessions = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for session in live_sessions:
        session.revoked_at = now
    db.add(
        _audit(
            user_id=user.id,
            action="PASSWORD_RESET_CONFIRM",
            description="Password reset completed; sessions revoked",
            severity=AuditSeverity.WARNING.value,
            context={"revoked_sessions": len(live_sessions)},
            ip=ip,
        )
    )
    await db.commit()

"""Authentication endpoints: login (JWT bearer) and current-user lookup."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbDep
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    verify_dummy_password,
    verify_password,
)
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.user import User
from app.schemas.auth import LoginIn, LoginOut, TokenOut, UserOut

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, request: Request, db: DbDep) -> LoginOut:
    """Authenticate by email or username and issue a JWT access token."""
    identifier = payload.identifier.strip()
    result = await db.execute(
        select(User).where(
            or_(
                func.lower(User.email) == identifier.lower(),
                User.username == identifier,
            )
        )
    )
    user = result.scalar_one_or_none()
    # Always spend bcrypt time, even when the account is missing or inactive,
    # so the no-user branch cannot be distinguished from a wrong password by
    # response latency (username/email-enumeration timing oracle).
    if user is None or not user.is_active:
        verify_dummy_password(payload.password)
        password_ok = False
    else:
        password_ok = verify_password(payload.password, user.password_hash)
    if not password_ok:
        logger.warning("Login failed", extra={"identifier": identifier})
        db.add(
            AuditLog(
                user_id=user.id if user is not None else None,
                event_type=AuditEventType.AUTH.value,
                action="LOGIN_FAILED",
                severity=AuditSeverity.WARNING.value,
                entity="user",
                entity_id=str(user.id) if user is not None else None,
                description="Failed login attempt",
                context=json.dumps({"identifier": identifier}),
                ip=request.client.host if request.client else None,
            )
        )
        await db.commit()
        raise AuthError("Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            user_id=user.id,
            event_type=AuditEventType.AUTH.value,
            action="LOGIN",
            severity=AuditSeverity.INFO.value,
            entity="user",
            entity_id=str(user.id),
            description=f"User {user.username} logged in",
            context="{}",
            ip=request.client.host if request.client else None,
        )
    )
    await db.commit()
    settings = get_settings()
    return LoginOut(
        token=TokenOut(
            access_token=create_access_token(user.id, user.role),
            expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        ),
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile."""
    return UserOut.model_validate(user)

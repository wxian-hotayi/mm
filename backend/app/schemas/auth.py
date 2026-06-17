"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    """Login request: ``identifier`` is an email address or a username."""

    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(
        min_length=1, max_length=320, description="Email address or username"
    )
    password: str = Field(min_length=1, max_length=256)
    remember: bool = Field(
        default=False,
        description="Extend the refresh-session lifetime (30d vs 1d).",
    )


class PasswordResetRequestIn(BaseModel):
    """Password-reset request: an email address (never enumerated)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class PasswordResetConfirmIn(BaseModel):
    """Password-reset confirmation: the raw token plus the new password."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=256)


class OkOut(BaseModel):
    """Trivial acknowledgement payload (``{"ok": true}``)."""

    ok: bool = True


class TokenOut(BaseModel):
    """Issued JWT access token; ``expires_in`` is the lifetime in seconds."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(BaseModel):
    """Public view of a user account."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    username: str
    role: str
    base_currency: str
    created_at: datetime
    last_login_at: datetime | None


class LoginOut(BaseModel):
    """Login response: the access token plus the authenticated user."""

    token: TokenOut
    user: UserOut

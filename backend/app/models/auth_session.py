"""Auth session and password-reset token models (DESIGN §6, §9, §20.8).

These two tables turn the Phase-1 JWT-bearer auth into the full §9 end-state:
DB-backed, rotating refresh sessions (``auth_sessions``) and single-use
password-reset tokens (``password_reset_tokens``). Both store only SHA-256
hashes of the opaque secret — the raw token never touches the database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    """A rotating refresh session bound to a user (DESIGN §9).

    The opaque refresh secret is stored only as its SHA-256 hex digest in
    ``refresh_token_hash`` (UNIQUE). Rotation revokes the old row (sets
    ``revoked_at``) and inserts a fresh one; logout revokes without reissue.
    A session is valid only while ``revoked_at IS NULL`` and ``expires_at`` is
    in the future.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    remember: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PasswordResetToken(Base):
    """A single-use password-reset token (DESIGN §9, 15-minute TTL).

    Only the SHA-256 hex digest of the opaque token is stored
    (``token_hash``, UNIQUE). A token is consumable only while ``used_at IS
    NULL`` and ``expires_at`` is in the future; confirming a reset stamps
    ``used_at`` so it cannot be replayed.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

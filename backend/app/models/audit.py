"""Audit log — mutations, auth events and behavioral flags (append-only)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEventType(str, enum.Enum):
    AUDIT = "AUDIT"
    BEHAVIOR_FLAG = "BEHAVIOR_FLAG"
    AUTH = "AUTH"
    IPS_ALERT = "IPS_ALERT"


class AuditSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_EVENT_TYPE_VALUES = ", ".join(
    f"'{event_type.value}'" for event_type in AuditEventType
)
_SEVERITY_VALUES = ", ".join(
    f"'{severity.value}'" for severity in AuditSeverity
)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_EVENT_TYPE_VALUES})", name="event_type"
        ),
        CheckConstraint(f"severity IN ({_SEVERITY_VALUES})", name="severity"),
        Index(
            "ix_audit_logs_user_id_event_type_created_at",
            "user_id",
            "event_type",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=AuditSeverity.INFO.value,
        server_default=text("'INFO'"),
    )
    entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    context: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

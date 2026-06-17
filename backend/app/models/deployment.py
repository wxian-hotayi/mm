"""Deployment queue — pending intents to move buffer cash into the market.

A :class:`DeploymentIntent` is queued when deployable surplus crosses the
threshold (``THRESHOLD``), is raised manually (``MANUAL``), or is opened for an
execution window (``WINDOW``). It is attached to an :class:`ExecutionPlan` when
planned and marked ``EXECUTED`` on execution (DESIGN §19.1). Intent status is
operational state, not derived portfolio state.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money


class DeploymentTrigger(str, enum.Enum):
    """What caused an intent to be enqueued."""

    THRESHOLD = "THRESHOLD"
    MANUAL = "MANUAL"
    WINDOW = "WINDOW"


class DeploymentStatus(str, enum.Enum):
    """Lifecycle of a queued deployment intent."""

    QUEUED = "QUEUED"
    PLANNED = "PLANNED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


_TRIGGER_VALUES = ", ".join(
    f"'{trigger.value}'" for trigger in DeploymentTrigger
)
_STATUS_VALUES = ", ".join(f"'{status.value}'" for status in DeploymentStatus)


class DeploymentIntent(Base):
    """A queued intent to deploy ``amount_myr`` of buffer cash into the market."""

    __tablename__ = "deployment_intents"
    __table_args__ = (
        CheckConstraint(f"trigger IN ({_TRIGGER_VALUES})", name="trigger"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # MYR amount intended for deployment.
    amount_myr: Mapped[Decimal] = mapped_column(Money, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DeploymentStatus.QUEUED.value,
        server_default=text("'QUEUED'"),
    )
    target_window_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    execution_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

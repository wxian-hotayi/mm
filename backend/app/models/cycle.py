"""Wealth Operating Cycle — history log of derived life-cycle states.

The current :class:`WealthCycleState` is a pure function of (date vs windows,
deployable cash, drift, active intents/plans) and is **never** stored
authoritatively (DESIGN §19.2, Decision Log 21). :class:`CycleStateLog` records
transitions for history/auditing only; a new row is appended only when the
derived state changes.
"""

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


class WealthCycleState(str, enum.Enum):
    """The derived wealth operating-cycle state (§19.2)."""

    ACCUMULATION = "ACCUMULATION"
    READY_TO_DEPLOY = "READY_TO_DEPLOY"
    DEPLOYMENT = "DEPLOYMENT"
    REBALANCE_WINDOW = "REBALANCE_WINDOW"


_STATE_VALUES = ", ".join(f"'{state.value}'" for state in WealthCycleState)


class CycleStateLog(Base):
    """Append-only log of wealth-cycle state transitions (history only)."""

    __tablename__ = "cycle_state_log"
    __table_args__ = (
        CheckConstraint(f"state IN ({_STATE_VALUES})", name="state"),
        Index(
            "ix_cycle_state_log_user_id_entered_at",
            "user_id",
            "entered_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # JSON snapshot of the signals that produced the state at transition time.
    context: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

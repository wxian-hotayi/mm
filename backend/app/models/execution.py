"""Execution plans — generated deploy/rebalance plans for an execution window.

An :class:`ExecutionPlan` captures a concrete, IPS-validated set of orders with
exact share quantities for a given window (DESIGN §19.6). JSON columns hold the
allocation snapshots, orders, human-readable steps and IPS violations. The plan
itself is operational state; the portfolio remains derived from the ledger and
is only affected once the plan's orders are recorded as transactions.

JSON columns store text (sqlite) / NUMERIC(18,4) is reserved for Money columns;
embedded monetary values inside JSON are serialized as strings by the service
layer so they round-trip losslessly to :class:`decimal.Decimal`.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class ExecutionPlanKind(str, enum.Enum):
    """What the plan does within its window."""

    DEPLOY = "DEPLOY"
    REBALANCE = "REBALANCE"
    DEPLOY_AND_REBALANCE = "DEPLOY_AND_REBALANCE"


class ExecutionPlanStatus(str, enum.Enum):
    """Lifecycle of an execution plan."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    EXPIRED = "EXPIRED"


_KIND_VALUES = ", ".join(f"'{kind.value}'" for kind in ExecutionPlanKind)
_STATUS_VALUES = ", ".join(
    f"'{status.value}'" for status in ExecutionPlanStatus
)


class ExecutionPlan(Base):
    """A deploy/rebalance plan for a single execution window (§19.6)."""

    __tablename__ = "execution_plans"
    __table_args__ = (
        CheckConstraint(f"plan_kind IN ({_KIND_VALUES})", name="plan_kind"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    window_date: Mapped[date] = mapped_column(Date, nullable=False)
    plan_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=ExecutionPlanStatus.DRAFT.value,
        server_default=text("'DRAFT'"),
    )
    # MYR deployed in this plan (0 if pure rebalance with no fresh cash).
    cash_deployed_myr: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    # USD equivalent of cash_deployed_myr at fx_rate_used.
    cash_deployed_usd: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    # USD->MYR rate used to convert deployed cash (NULL until known).
    fx_rate_used: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # JSON: {symbol: weight_pct} before/after the plan executes.
    allocation_before: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    allocation_after: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    # JSON list of orders [{symbol, side, quantity, unit_price_usd, ...}].
    orders: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    # JSON list of human-readable step strings.
    steps: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    ips_compliant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    # JSON list of IPS violations [{rule_type, level, message, evidence}].
    ips_violations: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

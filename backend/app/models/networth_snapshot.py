"""Net-worth cash snapshot — the REPORTING-layer capture of operational cash.

This table is distinct from the operational cash system (``cash_accounts`` /
``cash_movements``); it captures a point-in-time MYR cash position so historical
net worth is reconstructable independently of later cash movements (DESIGN
§19.4, Decision Log 23). Net Worth *references* cash via these snapshots; it
never owns or replaces the operational truth.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money


class NetWorthCashSnapshotSource(str, enum.Enum):
    """Whether the snapshot was auto-captured or manually entered."""

    AUTO = "auto"
    MANUAL = "manual"


_SOURCE_VALUES = ", ".join(
    f"'{source.value}'" for source in NetWorthCashSnapshotSource
)


class NetWorthCashSnapshot(Base):
    """A month-end (or ad-hoc) MYR cash capture for net-worth history (§19.4)."""

    __tablename__ = "net_worth_cash_snapshots"
    __table_args__ = (
        CheckConstraint(f"source IN ({_SOURCE_VALUES})", name="source"),
        UniqueConstraint("user_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    # MYR total cash captured at snapshot_date.
    total_cash_myr: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # JSON: {account_type: amount_myr} breakdown at snapshot time.
    breakdown: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=NetWorthCashSnapshotSource.AUTO.value,
        server_default=text("'auto'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

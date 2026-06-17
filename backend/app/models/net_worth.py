"""Net worth entries for non-portfolio assets and liabilities.

Amounts are always stored positive; ``LIABILITY`` entries are subtracted in
net-worth math, never stored negative.
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
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money


class NetWorthCategory(str, enum.Enum):
    CASH = "CASH"
    EMERGENCY_FUND = "EMERGENCY_FUND"
    BUSINESS = "BUSINESS"
    SAVINGS = "SAVINGS"
    OTHER_ASSET = "OTHER_ASSET"
    LIABILITY = "LIABILITY"


_CATEGORY_VALUES = ", ".join(
    f"'{category.value}'" for category in NetWorthCategory
)


class NetWorthEntry(Base):
    __tablename__ = "net_worth_entries"
    __table_args__ = (
        CheckConstraint(f"category IN ({_CATEGORY_VALUES})", name="category"),
        Index(
            "ix_net_worth_entries_user_id_entry_date",
            "user_id",
            "entry_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    amount_myr: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # Per Decision Log item 15: amounts are always stored positive; a
    # LIABILITY entry is flagged here and subtracted in net-worth math. The
    # column mirrors the ``LIABILITY`` category for entries that carry it.
    is_liability: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
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

"""Transaction ledger model — the single source of truth for the portfolio.

All derived state (holdings, NAV, drift, returns) is replayed from this
ledger on demand; nothing derived is ever stored.
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
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money


class TransactionType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"


_TYPE_VALUES = ", ".join(f"'{txn_type.value}'" for txn_type in TransactionType)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            f"transaction_type IN ({_TYPE_VALUES})", name="transaction_type"
        ),
        Index(
            "ix_transactions_user_id_transaction_date",
            "user_id",
            "transaction_date",
        ),
        Index(
            "ix_transactions_user_id_asset_symbol",
            "user_id",
            "asset_symbol",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    transaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    asset_symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    unit_price_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    fee_usd: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    fx_rate_recorded: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total_amount_myr: Mapped[Decimal] = mapped_column(Money, nullable=False)
    notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    import_hash: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True
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

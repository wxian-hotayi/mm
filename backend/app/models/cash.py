"""Cash buffer system — the operational MYR side of the wealth flow.

Models the path *salary -> GXBank (accumulate) -> transfer to Moomoo (FX) ->
broker DEPOSIT -> BUY* (DESIGN §19.1). Like the transaction ledger this layer
is **ledger-first**: account balances and deployable surplus are ALWAYS derived
from :class:`CashMovement` rows, never stored as authoritative state.

All amounts are MYR :class:`decimal.Decimal` stored via :class:`app.db.types.Money`
(exact, 4dp). Movement amounts are stored positive; the sign is applied by
:class:`CashMovementType` in the balance derivation (§19.1).
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
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money


class CashAccountType(str, enum.Enum):
    """Kind of operational cash account."""

    GXBANK = "GXBANK"
    SAVINGS = "SAVINGS"
    EMERGENCY_FUND = "EMERGENCY_FUND"
    BUSINESS = "BUSINESS"
    BROKER_CASH_MYR = "BROKER_CASH_MYR"
    OTHER = "OTHER"


class CashMovementType(str, enum.Enum):
    """Direction/intent of a cash movement.

    Sign applied in balance derivation (§19.1):
    ``+INFLOW +INTEREST +TRANSFER_IN`` ; ``-OUTFLOW -TRANSFER_OUT_TO_BROKER`` ;
    ``ADJUSTMENT`` is signed by the caller (stored positive; treated as a
    correction that may add or subtract — see the cash service).
    """

    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"
    INTEREST = "INTEREST"
    TRANSFER_OUT_TO_BROKER = "TRANSFER_OUT_TO_BROKER"
    TRANSFER_IN = "TRANSFER_IN"
    ADJUSTMENT = "ADJUSTMENT"


_ACCOUNT_TYPE_VALUES = ", ".join(
    f"'{account_type.value}'" for account_type in CashAccountType
)
_MOVEMENT_TYPE_VALUES = ", ".join(
    f"'{movement_type.value}'" for movement_type in CashMovementType
)


class CashAccount(Base):
    """An operational cash account (GXBank, savings, emergency fund, ...).

    ``is_buffer_source`` marks accounts whose balance counts toward deployable
    investment cash (GXBank yes, emergency fund no). ``target_buffer_myr`` is the
    minimum kept and never deployable.
    """

    __tablename__ = "cash_accounts"
    __table_args__ = (
        CheckConstraint(
            f"account_type IN ({_ACCOUNT_TYPE_VALUES})",
            name="account_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, default="MYR", server_default=text("'MYR'")
    )
    # Counts toward deployable investment cash (GXBank yes, emergency fund no).
    is_buffer_source: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # MYR. Minimum kept in the account; never part of deployable surplus.
    target_buffer_myr: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    # Informational annual interest percentage (GXBank daily-interest, pp).
    annual_interest_pct: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
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


class CashMovement(Base):
    """A single cash-account movement (ledger-first; balances derive from these).

    ``amount_myr`` is stored positive (MYR); the sign is applied by
    :class:`CashMovementType` during balance derivation. ``counterparty_account_id``
    links the two legs of a cash<->cash transfer; ``linked_transaction_id`` links a
    ``TRANSFER_OUT_TO_BROKER`` movement to the broker ``DEPOSIT`` it funds.
    """

    __tablename__ = "cash_movements"
    __table_args__ = (
        CheckConstraint(
            f"movement_type IN ({_MOVEMENT_TYPE_VALUES})",
            name="movement_type",
        ),
        Index(
            "ix_cash_movements_user_id_account_id_movement_date",
            "user_id",
            "account_id",
            "movement_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("cash_accounts.id", ondelete="CASCADE"), nullable=False
    )
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    # MYR, stored positive; sign applied by movement_type in §19.1 derivation.
    amount_myr: Mapped[Decimal] = mapped_column(Money, nullable=False)
    counterparty_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_accounts.id", ondelete="SET NULL"), nullable=True
    )
    linked_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

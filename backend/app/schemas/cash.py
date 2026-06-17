"""Cash Buffer System request/response schemas (DESIGN §19.1, §19.7).

Operational MYR cash accounts and their movement ledger. Request money values
arrive as ``str | int | float`` and are coerced to exact 4dp Decimals via the
Phase-1 :class:`~app.schemas.common.MoneyIn` family; response money values stay
Decimal and serialize to JSON numbers through ``MoneyOut``.

``CashSummaryOut`` mirrors the derived (never stored) figures from
:mod:`app.services.cash`: per-account balances, total cash, deployable surplus,
buffer fill ratio and the deployment-readiness state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.cash import CashAccount, CashAccountType, CashMovement, CashMovementType
from app.schemas.common import MoneyIn, MoneyOut, NonNegativeMoneyIn

_MAX_NAME_LENGTH = 120
_MAX_NOTES_LENGTH = 2000
_ZERO_4DP = Decimal("0.0000")


class CashAccountIn(BaseModel):
    """Create payload for an operational cash account.

    ``target_buffer_myr`` (MYR) is the minimum kept and never deployable;
    ``annual_interest_pct`` is informational (percentage points). Both must be
    non-negative.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    account_type: CashAccountType
    currency: str = Field(default="MYR", min_length=3, max_length=3)
    is_buffer_source: bool = False
    target_buffer_myr: NonNegativeMoneyIn = _ZERO_4DP
    annual_interest_pct: NonNegativeMoneyIn = _ZERO_4DP
    sort_order: int = Field(default=0, ge=0)


class CashAccountUpdate(BaseModel):
    """Partial update for a cash account; unset fields keep their values."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=_MAX_NAME_LENGTH)
    account_type: CashAccountType | None = None
    is_buffer_source: bool | None = None
    target_buffer_myr: NonNegativeMoneyIn | None = None
    annual_interest_pct: NonNegativeMoneyIn | None = None
    sort_order: int | None = Field(default=None, ge=0)


class CashAccountOut(BaseModel):
    """A cash account as stored (balances are reported via the summary)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    account_type: str
    currency: str
    is_buffer_source: bool
    target_buffer_myr: MoneyOut
    annual_interest_pct: MoneyOut
    sort_order: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, account: CashAccount) -> "CashAccountOut":
        return cls.model_validate(account)


class CashMovementIn(BaseModel):
    """Create payload for a cash movement (ledger-first; balances derive).

    ``amount_myr`` is stored positive for all types except ``ADJUSTMENT``,
    which may be signed to record a correction (validated in the service).
    """

    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    movement_type: CashMovementType
    amount_myr: MoneyIn
    movement_date: date
    counterparty_account_id: int | None = Field(default=None, gt=0)
    linked_transaction_id: int | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=_MAX_NOTES_LENGTH)


class CashMovementUpdate(BaseModel):
    """Partial update for a cash movement; unset fields keep their values."""

    model_config = ConfigDict(extra="forbid")

    movement_type: CashMovementType | None = None
    amount_myr: MoneyIn | None = None
    movement_date: date | None = None
    notes: str | None = Field(default=None, max_length=_MAX_NOTES_LENGTH)


class CashMovementOut(BaseModel):
    """A stored cash movement row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    movement_date: date
    movement_type: str
    amount_myr: MoneyOut
    counterparty_account_id: int | None
    linked_transaction_id: int | None
    notes: str
    created_at: datetime

    @classmethod
    def from_row(cls, movement: CashMovement) -> "CashMovementOut":
        return cls.model_validate(movement)


class CashAccountBalanceOut(CashAccountOut):
    """A cash account plus its derived MYR balance (§19.1)."""

    balance_myr: MoneyOut


class CashSummaryOut(BaseModel):
    """Derived cash position (§19.1, §19.7): per-account balances + surplus.

    ``deployable_surplus_myr`` is ``max(0, Σ buffer-source balance − Σ buffer
    target)``; ``buffer_fill_ratio`` is ``None`` when no target buffer is set
    (undefined); ``readiness`` is ``READY`` / ``ACCUMULATING``.
    """

    accounts: list[CashAccountBalanceOut]
    total_cash_myr: MoneyOut
    deployable_surplus_myr: MoneyOut
    buffer_fill_ratio: MoneyOut | None
    readiness: str
    as_of: date

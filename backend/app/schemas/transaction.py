"""Transaction request/response schemas with type-specific validation.

Request rules (enforced in :class:`TransactionIn`):

- ``BUY``/``SELL``: ``asset_symbol`` + ``quantity > 0`` +
  ``unit_price_usd >= 0``; the MYR total is server-derived
  ``(quantity x unit_price_usd + fee_usd) x fx_rate_recorded`` — clients
  must not send ``amount_usd`` or ``total_amount_myr``.
- ``DIVIDEND``: ``asset_symbol`` + exactly one of
  ``amount_usd | total_amount_myr``.
- ``DEPOSIT``/``WITHDRAWAL``/``FEE``: exactly one of
  ``amount_usd | total_amount_myr``; no ``asset_symbol``.
- ``fx_rate_recorded`` is always required and positive;
  ``transaction_date`` must not be in the future (Asia/Kuala_Lumpur).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.transaction import Transaction, TransactionType
from app.schemas.common import (
    MoneyIn,
    MoneyOut,
    NonNegativeMoneyIn,
    PositiveMoneyIn,
)
from app.utils.dates import kl_today
from app.utils.money import Q4, ZERO, safe_div

_TRADE_TYPES: Final[frozenset[TransactionType]] = frozenset(
    {TransactionType.BUY, TransactionType.SELL}
)
_SYMBOL_FREE_TYPES: Final[frozenset[TransactionType]] = frozenset(
    {
        TransactionType.DEPOSIT,
        TransactionType.WITHDRAWAL,
        TransactionType.FEE,
    }
)

_MAX_SYMBOL_LENGTH: Final[int] = 16
_MAX_NOTES_LENGTH: Final[int] = 2000


def _normalize_symbol(value: object) -> str | None:
    """Trim and uppercase a symbol; empty strings become None; reject symbols
    longer than :data:`_MAX_SYMBOL_LENGTH` characters after trimming."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("asset_symbol must be a string")
    cleaned = value.strip().upper()
    if len(cleaned) > _MAX_SYMBOL_LENGTH:
        raise ValueError(
            f"asset_symbol must be at most {_MAX_SYMBOL_LENGTH} characters"
        )
    return cleaned or None


def _reject_future_date(value: date) -> date:
    if value > kl_today():
        raise ValueError(
            "transaction_date must not be in the future "
            "(Asia/Kuala_Lumpur calendar)"
        )
    return value


class TransactionIn(BaseModel):
    """Create payload for one ledger transaction."""

    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType
    transaction_date: date
    asset_symbol: str | None = None
    quantity: MoneyIn | None = None
    unit_price_usd: MoneyIn | None = None
    fee_usd: NonNegativeMoneyIn = Decimal("0.0000")
    amount_usd: PositiveMoneyIn | None = None
    total_amount_myr: PositiveMoneyIn | None = None
    fx_rate_recorded: PositiveMoneyIn
    notes: str = Field(default="", max_length=_MAX_NOTES_LENGTH)

    @field_validator("asset_symbol", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str | None:
        return _normalize_symbol(value)

    @field_validator("transaction_date")
    @classmethod
    def _date_not_in_future(cls, value: date) -> date:
        return _reject_future_date(value)

    @model_validator(mode="after")
    def _enforce_type_rules(self) -> "TransactionIn":
        kind = self.transaction_type
        if kind in _TRADE_TYPES:
            if not self.asset_symbol:
                raise ValueError(f"{kind.value} requires asset_symbol")
            if self.quantity is None or self.quantity <= ZERO:
                raise ValueError(f"{kind.value} requires quantity > 0")
            if self.unit_price_usd is None or self.unit_price_usd < ZERO:
                raise ValueError(
                    f"{kind.value} requires unit_price_usd >= 0"
                )
            if self.amount_usd is not None or (
                self.total_amount_myr is not None
            ):
                raise ValueError(
                    f"{kind.value} amounts are derived server-side from "
                    "quantity, unit_price_usd, fee_usd and fx_rate_recorded; "
                    "do not send amount_usd or total_amount_myr"
                )
            return self
        if self.quantity is not None or self.unit_price_usd is not None:
            raise ValueError(
                f"{kind.value} must not include quantity or unit_price_usd"
            )
        if self.fee_usd != ZERO:
            raise ValueError(
                f"{kind.value} must not include fee_usd; record broker fees "
                "as a separate FEE transaction"
            )
        provided = [
            name
            for name in ("amount_usd", "total_amount_myr")
            if getattr(self, name) is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                f"{kind.value} requires exactly one of amount_usd or "
                "total_amount_myr"
            )
        if kind is TransactionType.DIVIDEND:
            if not self.asset_symbol:
                raise ValueError("DIVIDEND requires asset_symbol")
        elif self.asset_symbol is not None:
            raise ValueError(
                f"{kind.value} must not include asset_symbol"
            )
        return self

    def stored_total_amount_myr(self) -> Decimal:
        """MYR amount persisted on the row: authoritative for cash events,
        server-derived for trades (4dp, ROUND_HALF_UP)."""
        if self.transaction_type in _TRADE_TYPES:
            quantity = cast(Decimal, self.quantity)
            unit_price = cast(Decimal, self.unit_price_usd)
            return Q4(
                (quantity * unit_price + self.fee_usd)
                * self.fx_rate_recorded
            )
        if self.total_amount_myr is not None:
            return self.total_amount_myr
        return Q4(cast(Decimal, self.amount_usd) * self.fx_rate_recorded)


class TransactionUpdate(BaseModel):
    """Partial update payload; unset fields keep their stored values.

    When the update switches between the trade family (BUY/SELL) and the
    cash family, fields that no longer apply are dropped from the stored row
    and must be re-supplied where required — the merged payload is fully
    re-validated through :class:`TransactionIn`.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_type: TransactionType | None = None
    transaction_date: date | None = None
    asset_symbol: str | None = None
    quantity: MoneyIn | None = None
    unit_price_usd: MoneyIn | None = None
    fee_usd: NonNegativeMoneyIn | None = None
    amount_usd: PositiveMoneyIn | None = None
    total_amount_myr: PositiveMoneyIn | None = None
    fx_rate_recorded: PositiveMoneyIn | None = None
    notes: str | None = Field(default=None, max_length=_MAX_NOTES_LENGTH)

    @field_validator("asset_symbol", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str | None:
        return _normalize_symbol(value)

    @field_validator("transaction_date")
    @classmethod
    def _date_not_in_future(cls, value: date | None) -> date | None:
        if value is None:
            return None
        return _reject_future_date(value)

    @model_validator(mode="after")
    def _require_changes(self) -> "TransactionUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


def _derived_amount_usd(txn: Transaction) -> Decimal:
    """USD cash impact of a stored row (4dp): trade cost/proceeds including
    the fee, or the MYR amount converted at the recorded FX rate."""
    if txn.transaction_type == TransactionType.BUY.value:
        quantity = cast(Decimal, txn.quantity)
        unit_price = cast(Decimal, txn.unit_price_usd)
        return Q4(quantity * unit_price + txn.fee_usd)
    if txn.transaction_type == TransactionType.SELL.value:
        quantity = cast(Decimal, txn.quantity)
        unit_price = cast(Decimal, txn.unit_price_usd)
        return Q4(quantity * unit_price - txn.fee_usd)
    return Q4(safe_div(txn.total_amount_myr, txn.fx_rate_recorded))


def _output_fields(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "transaction_type": txn.transaction_type,
        "transaction_date": txn.transaction_date,
        "asset_symbol": txn.asset_symbol,
        "quantity": txn.quantity,
        "unit_price_usd": txn.unit_price_usd,
        "fee_usd": txn.fee_usd,
        "fx_rate_recorded": txn.fx_rate_recorded,
        "total_amount_myr": txn.total_amount_myr,
        "amount_usd": _derived_amount_usd(txn),
        "notes": txn.notes,
        "created_at": txn.created_at,
        "updated_at": txn.updated_at,
    }


class TransactionOut(BaseModel):
    """Stored transaction row plus the derived USD amount (never stored)."""

    id: int
    transaction_type: str
    transaction_date: date
    asset_symbol: str | None
    quantity: MoneyOut | None
    unit_price_usd: MoneyOut | None
    fee_usd: MoneyOut
    fx_rate_recorded: MoneyOut
    total_amount_myr: MoneyOut
    amount_usd: MoneyOut
    notes: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, txn: Transaction) -> "TransactionOut":
        return cls(**_output_fields(txn))


class TransactionWithWarningsOut(TransactionOut):
    """Mutation response: the stored row plus behavior warnings raised by
    the discipline engine and IPS warnings (INFO/WARN) raised by the
    enforcement engine for this ledger state. BLOCK-level IPS violations never
    reach here — they reject the request with HTTP 422 (unless overridden)."""

    behavior_warnings: list[str]
    ips_warnings: list[str]

    @classmethod
    def from_row_with_warnings(
        cls,
        txn: Transaction,
        warnings: list[str],
        ips_warnings: list[str] | None = None,
    ) -> "TransactionWithWarningsOut":
        return cls(
            **_output_fields(txn),
            behavior_warnings=warnings,
            ips_warnings=ips_warnings or [],
        )

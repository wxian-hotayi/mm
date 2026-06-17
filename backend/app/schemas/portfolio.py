"""Portfolio valuation and rebalance request/response schemas.

``ValuationOut``/``HoldingOut`` mirror the
:class:`~app.services.valuation.Valuation` dataclasses field-for-field;
``RebalanceOut`` mirrors :class:`~app.services.rebalance.RebalancePlan`
plus the current pre-trade weights. All percentages are on the 0-100 scale.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import (
    MoneyOut,
    NonNegativeMoneyIn,
    PositiveMoneyIn,
)


def _normalize_price_keys(
    prices: dict[str, Decimal],
) -> dict[str, Decimal]:
    """Uppercase and trim symbol keys; reject blank or duplicate symbols."""
    normalized: dict[str, Decimal] = {}
    for symbol, price in prices.items():
        cleaned = symbol.strip().upper()
        if not cleaned:
            raise ValueError("price symbols must not be blank")
        if cleaned in normalized:
            raise ValueError(f"duplicate price symbol {cleaned}")
        normalized[cleaned] = price
    return normalized


class ValuationIn(BaseModel):
    """Valuation request: USD prices per symbol and the USD->MYR FX rate.
    ``as_of`` limits the ledger replay to transactions on or before it."""

    model_config = ConfigDict(extra="forbid")

    prices: dict[str, PositiveMoneyIn]
    fx_rate: PositiveMoneyIn
    as_of: date | None = None

    @field_validator("prices", mode="after")
    @classmethod
    def _symbols(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        return _normalize_price_keys(value)


class HoldingOut(BaseModel):
    """Mirrors :class:`~app.services.valuation.HoldingValuation`."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: MoneyOut
    avg_cost_usd: MoneyOut
    cost_basis_usd: MoneyOut
    price_usd: MoneyOut
    market_value_usd: MoneyOut
    unrealized_usd: MoneyOut
    unrealized_pct: MoneyOut | None
    weight_pct: MoneyOut | None


class ValuationOut(BaseModel):
    """Mirrors :class:`~app.services.valuation.Valuation`."""

    model_config = ConfigDict(from_attributes=True)

    holdings: list[HoldingOut]
    cash_usd: MoneyOut
    nav_usd: MoneyOut
    nav_myr: MoneyOut
    fx_rate: MoneyOut
    cash_weight_pct: MoneyOut | None
    unrealized_usd: MoneyOut
    realized_usd: MoneyOut
    dividends_usd: MoneyOut
    fees_usd: MoneyOut
    total_pnl_usd: MoneyOut
    total_pnl_pct: MoneyOut | None
    net_deposits_usd: MoneyOut
    net_deposits_myr: MoneyOut


class RebalanceIn(BaseModel):
    """Rebalance request. ``additional_cash_usd`` is a planned contribution
    available to deploy; ``threshold_pct`` overrides the IPS drift
    threshold (percentage points) when provided."""

    model_config = ConfigDict(extra="forbid")

    prices: dict[str, PositiveMoneyIn]
    fx_rate: PositiveMoneyIn
    additional_cash_usd: NonNegativeMoneyIn = Decimal("0.0000")
    threshold_pct: PositiveMoneyIn | None = None

    @field_validator("prices", mode="after")
    @classmethod
    def _symbols(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        return _normalize_price_keys(value)


class RebalanceOrderOut(BaseModel):
    """Mirrors :class:`~app.services.rebalance.RebalanceOrder`."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    side: str
    quantity: MoneyOut
    unit_price_usd: MoneyOut
    est_amount_usd: MoneyOut
    est_amount_myr: MoneyOut


class RebalanceOut(BaseModel):
    """Rebalance plan: orders, human-readable steps, pre/post-trade weights
    (0-100 scale, including ``"CASH"``) and the funding-priority note."""

    status: str
    orders: list[RebalanceOrderOut]
    steps: list[str]
    current_weights: dict[str, MoneyOut]
    post_trade_weights: dict[str, MoneyOut]
    leftover_cash_usd: MoneyOut
    max_drift_pp: MoneyOut
    priority_note: str
    message: str

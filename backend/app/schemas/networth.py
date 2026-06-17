"""Net Worth reporting-aggregate schemas (DESIGN §19.4, §19.7).

Portfolio is a subset of Net Worth. These response models mirror the
:mod:`app.services.networth` dataclasses field-for-field; request prices/FX use
the Phase-1 ``MoneyIn`` family, and all MYR/USD figures serialize to JSON
numbers through ``MoneyOut``. ``weight_pct`` fields are on the 0–100 scale.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import MoneyOut, PositiveMoneyIn
from app.schemas.portfolio import _normalize_price_keys
from app.services import networth as networth_service


class NetWorthQueryIn(BaseModel):
    """Optional pricing inputs for the Net Worth aggregate.

    ``prices`` (USD per held symbol) and ``fx_rate`` (USD->MYR) price the live
    investment leg; when omitted the investment leg is reported as zero with
    ``portfolio.priced = False`` (never crashes, §19.4). ``as_of`` limits the
    ledger / cash replay to that date.
    """

    model_config = ConfigDict(extra="forbid")

    prices: dict[str, PositiveMoneyIn] | None = None
    fx_rate: PositiveMoneyIn | None = None
    as_of: date | None = None

    @field_validator("prices", mode="after")
    @classmethod
    def _symbols(cls, value: dict | None) -> dict | None:
        return _normalize_price_keys(value) if value is not None else None


class PortfolioSubsetOut(BaseModel):
    """The investment subset of Net Worth (mirrors ``PortfolioSubset``)."""

    model_config = ConfigDict(from_attributes=True)

    nav_usd: MoneyOut
    nav_myr: MoneyOut
    holdings_count: int
    fx_rate: MoneyOut | None
    priced: bool


class BreakdownItemOut(BaseModel):
    """One Net Worth category line (mirrors ``BreakdownItem``)."""

    model_config = ConfigDict(from_attributes=True)

    category: str
    amount_myr: MoneyOut
    weight_pct: MoneyOut | None
    source: str


class NetWorthChangeOut(BaseModel):
    """Absolute (MYR) + percentage change vs a baseline (mirrors ``NetWorthChange``)."""

    model_config = ConfigDict(from_attributes=True)

    abs_myr: MoneyOut
    pct: MoneyOut | None


class NetWorthSummaryOut(BaseModel):
    """Full Net Worth reporting aggregate (mirrors ``NetWorthSummary``).

    ``total_net_worth_myr`` = investment + cash + other assets − liabilities;
    ``breakdown`` is ordered INVESTMENT, CASH, EMERGENCY_FUND, BUSINESS,
    OTHER_ASSET, LIABILITY. ``change_1m`` / ``change_1y`` are ``None`` until
    history exists.
    """

    model_config = ConfigDict(from_attributes=True)

    as_of: date
    total_net_worth_myr: MoneyOut
    investment_myr: MoneyOut
    cash_myr: MoneyOut
    other_assets_myr: MoneyOut
    liabilities_myr: MoneyOut
    breakdown: list[BreakdownItemOut]
    portfolio: PortfolioSubsetOut
    deployable_surplus_myr: MoneyOut
    change_1m: NetWorthChangeOut | None
    change_1y: NetWorthChangeOut | None

    @classmethod
    def from_summary(
        cls, summary: "networth_service.NetWorthSummary"
    ) -> "NetWorthSummaryOut":
        return cls.model_validate(summary)


class NetWorthBreakdownOut(BaseModel):
    """Category breakdown only (the ``GET /networth/breakdown`` response)."""

    as_of: date
    total_net_worth_myr: MoneyOut
    breakdown: list[BreakdownItemOut]

    @classmethod
    def from_summary(
        cls, summary: "networth_service.NetWorthSummary"
    ) -> "NetWorthBreakdownOut":
        return cls(
            as_of=summary.as_of,
            total_net_worth_myr=summary.total_net_worth_myr,
            breakdown=[
                BreakdownItemOut.model_validate(item)
                for item in summary.breakdown
            ],
        )

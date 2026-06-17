"""Portfolio valuation — prices a replayed ledger state.

Pure function over a :class:`~app.services.ledger.LedgerState`, a USD price
per held symbol and the current USD->MYR FX rate. No database access.

Units: ``*_usd`` are USD (4dp), ``nav_myr`` is MYR quantized to 2dp for
display (intermediate MYR math stays at 4dp), and ``*_pct`` fields are
percentages on the 0–100 scale (e.g. ``Decimal('70.0000')`` = 70%), ``None``
when the denominator is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.core.errors import ValidationFailed
from app.services.ledger import LedgerState
from app.utils.money import Q2, Q4, ZERO

_HUNDRED: Final[Decimal] = Decimal("100")


@dataclass(frozen=True)
class HoldingValuation:
    """Valuation of one held symbol (USD; percentages on the 0–100 scale)."""

    symbol: str
    quantity: Decimal
    avg_cost_usd: Decimal
    cost_basis_usd: Decimal
    price_usd: Decimal
    market_value_usd: Decimal
    unrealized_usd: Decimal
    unrealized_pct: Decimal | None
    weight_pct: Decimal | None


@dataclass(frozen=True)
class Valuation:
    """Full portfolio valuation snapshot.

    ``nav_usd`` is USD (4dp); ``nav_myr`` is MYR (2dp display). Totals are
    USD. ``total_pnl_usd = unrealized + realized + dividends - fees``;
    ``total_pnl_pct`` is measured against ``net_deposits_usd`` (0–100 scale),
    ``None`` when there are no net deposits.
    """

    holdings: list[HoldingValuation]
    cash_usd: Decimal
    nav_usd: Decimal
    nav_myr: Decimal
    fx_rate: Decimal
    cash_weight_pct: Decimal | None
    unrealized_usd: Decimal
    realized_usd: Decimal
    dividends_usd: Decimal
    fees_usd: Decimal
    total_pnl_usd: Decimal
    total_pnl_pct: Decimal | None
    net_deposits_usd: Decimal
    net_deposits_myr: Decimal


def valuation(
    state: LedgerState, prices: dict[str, Decimal], fx_rate: Decimal
) -> Valuation:
    """Price ``state`` with per-symbol USD ``prices`` and the USD->MYR
    ``fx_rate``.

    Raises :class:`ValidationFailed` when the FX rate is not positive, when
    any held symbol is missing from ``prices`` (all offenders listed), or
    when a supplied price for a held symbol is not positive.
    """
    if fx_rate <= ZERO:
        raise ValidationFailed("FX rate (USD->MYR) must be positive")
    held = sorted(
        (
            position
            for position in state.positions.values()
            if position.quantity > ZERO
        ),
        key=lambda position: position.symbol,
    )
    missing = sorted(
        position.symbol for position in held if position.symbol not in prices
    )
    if missing:
        raise ValidationFailed(
            f"Missing USD price for held symbols: {', '.join(missing)}"
        )
    non_positive = sorted(
        position.symbol for position in held if prices[position.symbol] <= ZERO
    )
    if non_positive:
        raise ValidationFailed(
            "USD price must be positive for symbols: "
            f"{', '.join(non_positive)}"
        )

    market_values: dict[str, Decimal] = {
        position.symbol: Q4(position.quantity * prices[position.symbol])
        for position in held
    }
    nav_usd = Q4(
        sum(market_values.values(), start=ZERO) + state.cash_usd
    )

    holdings: list[HoldingValuation] = []
    total_unrealized = ZERO
    for position in held:
        market_value = market_values[position.symbol]
        unrealized = market_value - position.cost_basis_usd
        total_unrealized += unrealized
        unrealized_pct = (
            Q4(unrealized * _HUNDRED / position.cost_basis_usd)
            if position.cost_basis_usd > ZERO
            else None
        )
        weight_pct = (
            Q4(market_value * _HUNDRED / nav_usd) if nav_usd > ZERO else None
        )
        holdings.append(
            HoldingValuation(
                symbol=position.symbol,
                quantity=position.quantity,
                avg_cost_usd=position.avg_cost_usd,
                cost_basis_usd=position.cost_basis_usd,
                price_usd=prices[position.symbol],
                market_value_usd=market_value,
                unrealized_usd=unrealized,
                unrealized_pct=unrealized_pct,
                weight_pct=weight_pct,
            )
        )

    cash_weight_pct = (
        Q4(state.cash_usd * _HUNDRED / nav_usd) if nav_usd > ZERO else None
    )
    total_pnl_usd = (
        total_unrealized
        + state.realized_gain_usd
        + state.dividends_usd
        - state.fees_usd
    )
    total_pnl_pct = (
        Q4(total_pnl_usd * _HUNDRED / state.net_deposits_usd)
        if state.net_deposits_usd > ZERO
        else None
    )
    return Valuation(
        holdings=holdings,
        cash_usd=state.cash_usd,
        nav_usd=nav_usd,
        nav_myr=Q2(nav_usd * fx_rate),
        fx_rate=fx_rate,
        cash_weight_pct=cash_weight_pct,
        unrealized_usd=total_unrealized,
        realized_usd=state.realized_gain_usd,
        dividends_usd=state.dividends_usd,
        fees_usd=state.fees_usd,
        total_pnl_usd=total_pnl_usd,
        total_pnl_pct=total_pnl_pct,
        net_deposits_usd=state.net_deposits_usd,
        net_deposits_myr=state.net_deposits_myr,
    )

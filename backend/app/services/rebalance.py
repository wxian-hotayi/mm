"""Rebalance recommendation engine (DESIGN section 7.4).

Pure function — no database access. Funding priority is encoded explicitly:
idle cash first, then the planned contribution (``extra_cash_usd``), and
selling existing holdings only when targets cannot be reached with cash.

Units: ``*_usd`` are USD (4dp), ``est_amount_myr`` is MYR (2dp display),
weights are percentages on the 0–100 scale, share quantities are 4dp
(BUY quantities rounded DOWN so the plan can never overspend).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final

from app.core.errors import ValidationFailed
from app.models.ips import IpsRule
from app.services.drift import drift, parse_target_weights
from app.services.ledger import LedgerState
from app.services.valuation import valuation
from app.utils.money import Q2, Q4, ZERO, safe_div

_HUNDRED: Final[Decimal] = Decimal("100")
_ONE: Final[Decimal] = Decimal("1")
_VALUE_TOLERANCE_USD: Final[Decimal] = Decimal("0.005")
_SHARE_QUANTUM: Final[Decimal] = Decimal("0.0001")
_TARGET_SUM_TOLERANCE: Final[Decimal] = Decimal("0.0001")

PRIORITY_NOTE: Final[str] = (
    "Funding priority: deploy idle cash first, then the planned "
    "contribution; sell existing holdings only when targets cannot be "
    "reached with cash alone. Selling is the last resort — it can realize "
    "taxable gains and breaks the buy-and-hold discipline."
)


class RebalanceStatus(str, enum.Enum):
    NO_ACTION = "NO_ACTION"
    CASH_ONLY = "CASH_ONLY"
    SELL_REQUIRED = "SELL_REQUIRED"


@dataclass(frozen=True)
class RebalanceOrder:
    """One recommended order. ``side`` is ``"BUY"`` or ``"SELL"``; quantities
    are 4dp shares; ``est_amount_usd`` is USD (4dp); ``est_amount_myr`` is MYR
    (2dp) at the supplied FX rate."""

    symbol: str
    side: str
    quantity: Decimal
    unit_price_usd: Decimal
    est_amount_usd: Decimal
    est_amount_myr: Decimal


@dataclass(frozen=True)
class RebalancePlan:
    """Rebalance recommendation.

    ``post_trade_weights_pct`` maps each symbol (plus ``"CASH"``) to its
    projected post-trade weight on the 0–100 scale; ``leftover_cash_usd`` is
    the projected un-deployed cash after all orders execute.
    """

    status: RebalanceStatus
    orders: list[RebalanceOrder]
    steps: list[str]
    post_trade_weights_pct: dict[str, Decimal]
    leftover_cash_usd: Decimal
    max_abs_drift_pp: Decimal
    priority_note: str
    message: str


def _floor_shares(amount_usd: Decimal, price_usd: Decimal) -> Decimal:
    """BUY share quantity: amount/price rounded DOWN to 4dp (never overspend)."""
    return safe_div(amount_usd, price_usd).quantize(
        _SHARE_QUANTUM, rounding=ROUND_DOWN
    )


def _exact_shares(amount_usd: Decimal, price_usd: Decimal) -> Decimal:
    """SELL share quantity: amount/price quantized to exactly 4dp."""
    return Q4(safe_div(amount_usd, price_usd))


def _order(
    symbol: str,
    side: str,
    quantity: Decimal,
    price_usd: Decimal,
    fx_rate: Decimal,
) -> RebalanceOrder:
    est_usd = Q4(quantity * price_usd)
    return RebalanceOrder(
        symbol=symbol,
        side=side,
        quantity=quantity,
        unit_price_usd=price_usd,
        est_amount_usd=est_usd,
        est_amount_myr=Q2(est_usd * fx_rate),
    )


def _steps_for(orders: list[RebalanceOrder]) -> list[str]:
    return [
        (
            f"{index}. {order.side.capitalize()} {order.quantity} "
            f"{order.symbol} @ ${order.unit_price_usd} ≈ "
            f"${order.est_amount_usd} (RM{order.est_amount_myr})"
        )
        for index, order in enumerate(orders, start=1)
    ]


def _post_trade_weights(
    values_usd: dict[str, Decimal], cash_usd: Decimal
) -> tuple[dict[str, Decimal], Decimal]:
    """Projected post-trade weights (0–100 scale, incl. ``"CASH"``) and NAV."""
    nav = sum(values_usd.values(), start=ZERO) + cash_usd
    if nav <= ZERO:
        return {}, nav
    weights = {
        symbol: Q4(value * _HUNDRED / nav)
        for symbol, value in sorted(values_usd.items())
    }
    weights["CASH"] = Q4(cash_usd * _HUNDRED / nav)
    return weights, nav


def plan_rebalance(
    state: LedgerState,
    prices: dict[str, Decimal],
    fx_rate: Decimal,
    ips: IpsRule,
    extra_cash_usd: Decimal = ZERO,
    threshold_pct: Decimal | None = None,
    *,
    sell_disabled: bool = False,
) -> RebalancePlan:
    """Build a rebalance plan from a replayed ledger state.

    ``prices`` are USD per share for every held and target symbol;
    ``fx_rate`` is USD->MYR (for MYR estimates only); ``extra_cash_usd`` is a
    planned contribution available to deploy (USD, must be >= 0);
    ``threshold_pct`` overrides the IPS drift threshold (percentage points)
    when provided. ``sell_disabled`` forces the CASH_ONLY path — buys only,
    floored strictly against deployable cash (cash + contribution) so the plan
    can never overspend — used by deploy-only execution windows (§19.6) where
    selling is forbidden (additive deployment only).

    Statuses:
      - ``NO_ACTION``: drift within threshold, cash drag within policy and no
        contribution to deploy.
      - ``CASH_ONLY``: every target delta is a BUY fundable from deployable
        cash (tolerance $0.005); BUY shares are rounded DOWN to 4dp. Also the
        status when ``sell_disabled`` is set (buys floored against cash only).
      - ``SELL_REQUIRED``: overweight positions must be trimmed (exact 4dp
        sell quantities, capped at held shares) before buying underweights.
        Never returned when ``sell_disabled`` is set.

    Raises :class:`ValidationFailed` on negative extra cash, a non-positive
    threshold override, target weights summing above 1, or missing/invalid
    prices for any held or target symbol.
    """
    if extra_cash_usd < ZERO:
        raise ValidationFailed("extra_cash_usd must not be negative")
    threshold = (
        threshold_pct if threshold_pct is not None else ips.drift_threshold_pct
    )
    if threshold <= ZERO:
        raise ValidationFailed("Drift threshold must be positive")
    targets = parse_target_weights(ips.target_weights)
    if sum(targets.values(), start=ZERO) > _ONE + _TARGET_SUM_TOLERANCE:
        raise ValidationFailed("IPS target weights must sum to at most 1")
    missing = sorted(symbol for symbol in targets if symbol not in prices)
    if missing:
        raise ValidationFailed(
            f"Missing USD price for target symbols: {', '.join(missing)}"
        )
    non_positive = sorted(
        symbol for symbol in targets if prices[symbol] <= ZERO
    )
    if non_positive:
        raise ValidationFailed(
            "USD price must be positive for symbols: "
            f"{', '.join(non_positive)}"
        )

    current = valuation(state, prices, fx_rate)
    drift_report = drift(current, ips)
    within_threshold = drift_report.max_abs_drift_pp <= threshold
    cash_weight_pct = (
        current.cash_weight_pct
        if current.cash_weight_pct is not None
        else ZERO
    )
    cash_within_policy = cash_weight_pct <= ips.max_cash_drag_pct

    values_usd: dict[str, Decimal] = {
        holding.symbol: holding.market_value_usd
        for holding in current.holdings
    }
    symbols = sorted(set(targets) | set(values_usd))
    cash_usd = state.cash_usd
    deployable_usd = cash_usd + extra_cash_usd
    investable_usd = (
        sum(values_usd.values(), start=ZERO) + deployable_usd
    )

    if investable_usd <= ZERO or (
        within_threshold and cash_within_policy and extra_cash_usd == ZERO
    ):
        if investable_usd <= ZERO:
            message = (
                "Nothing to rebalance: the portfolio has no investable value."
            )
        else:
            message = (
                f"Max drift {drift_report.max_abs_drift_pp}pp is within the "
                f"{threshold}pp threshold and cash weight {cash_weight_pct}% "
                f"is within the {ips.max_cash_drag_pct}% policy. Nothing to "
                "do — discipline wins."
            )
        weights, _ = _post_trade_weights(values_usd, deployable_usd)
        return RebalancePlan(
            status=RebalanceStatus.NO_ACTION,
            orders=[],
            steps=["1. Do nothing. The allocation is within policy."],
            post_trade_weights_pct=weights,
            leftover_cash_usd=Q4(deployable_usd),
            max_abs_drift_pp=drift_report.max_abs_drift_pp,
            priority_note=PRIORITY_NOTE,
            message=message,
        )

    deltas_usd: dict[str, Decimal] = {
        symbol: targets.get(symbol, ZERO) * investable_usd
        - values_usd.get(symbol, ZERO)
        for symbol in symbols
    }

    cash_only = sell_disabled or all(
        delta >= -_VALUE_TOLERANCE_USD for delta in deltas_usd.values()
    )
    orders: list[RebalanceOrder] = []
    if cash_only:
        # Buys are floored against the cash actually available (never the raw
        # target delta) so a deploy-only / cash-only plan can never overspend
        # the deployable budget, even when targets would require selling. In
        # sell-disabled (deploy-only) mode the budget is strictly the fresh
        # contribution (``extra_cash_usd`` — the deployable surplus, §19.6); a
        # normal cash-only rebalance also folds in pre-existing idle cash.
        available_usd = extra_cash_usd if sell_disabled else deployable_usd
        for symbol in symbols:
            delta = deltas_usd[symbol]
            if delta <= _VALUE_TOLERANCE_USD:
                continue
            fundable_usd = delta if delta <= available_usd else available_usd
            quantity = _floor_shares(fundable_usd, prices[symbol])
            if quantity > ZERO:
                buy_order = _order(
                    symbol, "BUY", quantity, prices[symbol], fx_rate
                )
                orders.append(buy_order)
                available_usd -= buy_order.est_amount_usd
        status = RebalanceStatus.CASH_ONLY
        message = (
            "Targets are reachable with cash alone: buying underweight "
            "holdings with deployable cash of "
            f"${Q4(deployable_usd)} (no selling required)."
        )
    else:
        available_usd = deployable_usd
        for symbol in symbols:
            delta = deltas_usd[symbol]
            if delta >= -_VALUE_TOLERANCE_USD:
                continue
            held_quantity = (
                state.positions[symbol].quantity
                if symbol in state.positions
                else ZERO
            )
            quantity = min(
                _exact_shares(-delta, prices[symbol]), held_quantity
            )
            if quantity > ZERO:
                sell_order = _order(
                    symbol, "SELL", quantity, prices[symbol], fx_rate
                )
                orders.append(sell_order)
                available_usd += sell_order.est_amount_usd
        for symbol in symbols:
            delta = deltas_usd[symbol]
            if delta <= _VALUE_TOLERANCE_USD:
                continue
            # Floor each BUY against the cash actually available after the
            # (rounded/capped) sells — never against the target delta — so the
            # plan can never overspend deployable cash. The target delta only
            # caps how much of a holding we top up; the running cash figure
            # caps what we can afford.
            fundable_usd = delta if delta <= available_usd else available_usd
            quantity = _floor_shares(fundable_usd, prices[symbol])
            if quantity > ZERO:
                buy_order = _order(
                    symbol, "BUY", quantity, prices[symbol], fx_rate
                )
                orders.append(buy_order)
                available_usd -= buy_order.est_amount_usd
        status = RebalanceStatus.SELL_REQUIRED
        message = (
            "Cash and planned contributions are insufficient to reach the "
            "targets: overweight holdings must be trimmed before buying "
            "underweights."
        )

    if not orders:
        weights, _ = _post_trade_weights(values_usd, deployable_usd)
        return RebalancePlan(
            status=RebalanceStatus.NO_ACTION,
            orders=[],
            steps=["1. Do nothing. Required adjustments round to zero shares."],
            post_trade_weights_pct=weights,
            leftover_cash_usd=Q4(deployable_usd),
            max_abs_drift_pp=drift_report.max_abs_drift_pp,
            priority_note=PRIORITY_NOTE,
            message=(
                "Computed adjustments are below the 0.0001-share order "
                "resolution; no orders issued."
            ),
        )

    post_values = dict(values_usd)
    post_cash = deployable_usd
    for order in orders:
        current_value = post_values.get(order.symbol, ZERO)
        if order.side == "BUY":
            post_values[order.symbol] = current_value + order.est_amount_usd
            post_cash -= order.est_amount_usd
        else:
            post_values[order.symbol] = current_value - order.est_amount_usd
            post_cash += order.est_amount_usd
    post_weights, _ = _post_trade_weights(post_values, post_cash)

    return RebalancePlan(
        status=status,
        orders=orders,
        steps=_steps_for(orders),
        post_trade_weights_pct=post_weights,
        leftover_cash_usd=Q4(post_cash),
        max_abs_drift_pp=drift_report.max_abs_drift_pp,
        priority_note=PRIORITY_NOTE,
        message=message,
    )

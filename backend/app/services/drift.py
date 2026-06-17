"""Allocation drift vs the Investment Policy Statement targets.

Pure functions over a :class:`~app.services.valuation.Valuation` and the
user's single :class:`~app.models.ips.IpsRule` policy row. No database access.

Units: ``weight_pct``/``target_pct`` are percentages on the 0–100 scale;
``drift_pp`` and ``cash_drag_pp`` are percentage points (weight − target).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.core.errors import ValidationFailed
from app.models.ips import IpsRule
from app.services.valuation import Valuation
from app.utils.money import D, Q4, ZERO

_HUNDRED: Final[Decimal] = Decimal("100")


@dataclass(frozen=True)
class DriftItem:
    """Per-symbol drift: actual weight vs IPS target (0–100 scale; ``drift_pp``
    in percentage points, positive = overweight)."""

    symbol: str
    weight_pct: Decimal
    target_pct: Decimal
    drift_pp: Decimal


@dataclass(frozen=True)
class DriftReport:
    """Drift across the whole portfolio.

    ``max_abs_drift_pp`` is the largest absolute per-symbol drift in
    percentage points; ``within_threshold`` compares it against the IPS
    ``drift_threshold_pct``. ``cash_drag_pp`` is cash weight minus
    ``max_cash_drag_pct`` (positive = excess idle cash above policy).
    """

    items: list[DriftItem]
    max_abs_drift_pp: Decimal
    within_threshold: bool
    cash_drag_pp: Decimal


def parse_target_weights(raw: str) -> dict[str, Decimal]:
    """Parse the IPS ``target_weights`` JSON (e.g. ``{"VOO": "0.70"}``) into
    exact Decimal fractions (0–1 scale) keyed by symbol.

    Values may be JSON strings or numbers; numbers are parsed losslessly via
    Decimal. Raises :class:`ValidationFailed` on malformed JSON, an empty
    mapping, negative weights, or a zero total.
    """
    try:
        parsed = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailed(
            "IPS target_weights is not valid JSON"
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValidationFailed(
            "IPS target_weights must be a non-empty JSON object of "
            "symbol -> weight"
        )
    weights: dict[str, Decimal] = {}
    for symbol, value in parsed.items():
        if isinstance(value, Decimal):
            weight = value
        elif isinstance(value, str):
            weight = D(value)
        else:
            raise ValidationFailed(
                f"IPS target weight for {symbol!r} must be a decimal string "
                "or number"
            )
        if weight < ZERO:
            raise ValidationFailed(
                f"IPS target weight for {symbol!r} must not be negative"
            )
        weights[str(symbol).upper()] = weight
    if sum(weights.values(), start=ZERO) == ZERO:
        raise ValidationFailed("IPS target weights must not all be zero")
    return weights


def drift(valuation: Valuation, ips: IpsRule) -> DriftReport:
    """Compute per-symbol drift of ``valuation`` against the IPS targets.

    Covers the union of held symbols and target symbols: a held symbol absent
    from the targets gets a 0% target; a target symbol not held gets a 0%
    weight. When NAV is zero all weights are treated as 0%.
    """
    targets = parse_target_weights(ips.target_weights)
    weights: dict[str, Decimal] = {
        holding.symbol: (
            holding.weight_pct if holding.weight_pct is not None else ZERO
        )
        for holding in valuation.holdings
    }
    symbols = sorted(set(targets) | set(weights))
    items: list[DriftItem] = []
    for symbol in symbols:
        weight_pct = weights.get(symbol, ZERO)
        target_pct = Q4(targets.get(symbol, ZERO) * _HUNDRED)
        items.append(
            DriftItem(
                symbol=symbol,
                weight_pct=weight_pct,
                target_pct=target_pct,
                drift_pp=Q4(weight_pct - target_pct),
            )
        )
    max_abs_drift_pp = max(
        (abs(item.drift_pp) for item in items), default=ZERO
    )
    cash_weight_pct = (
        valuation.cash_weight_pct
        if valuation.cash_weight_pct is not None
        else ZERO
    )
    return DriftReport(
        items=items,
        max_abs_drift_pp=max_abs_drift_pp,
        within_threshold=max_abs_drift_pp <= ips.drift_threshold_pct,
        cash_drag_pp=Q4(cash_weight_pct - ips.max_cash_drag_pct),
    )

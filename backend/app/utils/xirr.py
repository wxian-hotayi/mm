"""XIRR (money-weighted annualized return) solver.

Newton's method with a bisection fallback. Root-finding internals operate on
floats — this is the single sanctioned float zone for the *dimensionless*
annual rate (owner directive: floats never touch stored money, shares or FX
values). Inputs are Decimal cash flows; the result is returned as a Decimal
quantized to 6 decimal places.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

_DAYS_PER_YEAR: Final[float] = 365.0
_INITIAL_GUESS: Final[float] = 0.1
_MAX_NEWTON_ITERATIONS: Final[int] = 100
_STEP_TOLERANCE: Final[float] = 1e-9
_BISECT_LOW: Final[float] = -0.9999
_BISECT_HIGH: Final[float] = 10.0
_MAX_BISECT_ITERATIONS: Final[int] = 300
_SIX_DP: Final[Decimal] = Decimal("0.000001")


def _npv(rate: float, times: list[float], amounts: list[float]) -> float:
    """Net present value of the flows at an annual ``rate`` (times in years)."""
    base = 1.0 + rate
    return sum(
        amount * base ** (-t) for t, amount in zip(times, amounts)
    )


def _npv_derivative(
    rate: float, times: list[float], amounts: list[float]
) -> float:
    """d(NPV)/d(rate) of the flows at an annual ``rate``."""
    base = 1.0 + rate
    return sum(
        -t * amount * base ** (-t - 1.0) for t, amount in zip(times, amounts)
    )


def _npv_tolerance(amounts: list[float]) -> float:
    """Acceptance tolerance for a candidate root, scaled to flow magnitude."""
    return 1e-6 * max(1.0, sum(abs(amount) for amount in amounts))


def _solve_newton(times: list[float], amounts: list[float]) -> float | None:
    """Newton iteration from 0.1; None when it diverges or leaves rate > -1."""
    rate = _INITIAL_GUESS
    for _ in range(_MAX_NEWTON_ITERATIONS):
        if 1.0 + rate <= 0.0:
            return None
        value = _npv(rate, times, amounts)
        derivative = _npv_derivative(rate, times, amounts)
        if (
            not math.isfinite(value)
            or not math.isfinite(derivative)
            or derivative == 0.0
        ):
            return None
        next_rate = rate - value / derivative
        if not math.isfinite(next_rate) or 1.0 + next_rate <= 0.0:
            return None
        if abs(next_rate - rate) < _STEP_TOLERANCE:
            residual = _npv(next_rate, times, amounts)
            if math.isfinite(residual) and abs(residual) <= _npv_tolerance(
                amounts
            ):
                return next_rate
            return None
        rate = next_rate
    return None


def _solve_bisection(times: list[float], amounts: list[float]) -> float | None:
    """Bisection on [-0.9999, 10.0]; None when no sign change brackets a root."""
    low, high = _BISECT_LOW, _BISECT_HIGH
    f_low = _npv(low, times, amounts)
    f_high = _npv(high, times, amounts)
    if not math.isfinite(f_low) or not math.isfinite(f_high):
        return None
    if f_low == 0.0:
        return low
    if f_high == 0.0:
        return high
    if (f_low > 0.0) == (f_high > 0.0):
        return None
    for _ in range(_MAX_BISECT_ITERATIONS):
        mid = (low + high) / 2.0
        f_mid = _npv(mid, times, amounts)
        if not math.isfinite(f_mid):
            return None
        if f_mid == 0.0 or (high - low) / 2.0 < _STEP_TOLERANCE:
            return mid
        if (f_mid > 0.0) == (f_low > 0.0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2.0


def xirr(flows: list[tuple[date, Decimal]]) -> Decimal | None:
    """Annualized money-weighted return (dimensionless decimal fraction,
    e.g. ``Decimal('0.090000')`` = +9% p.a.) of dated Decimal cash flows.

    Sign convention is the investor's: contributions negative, proceeds and
    the terminal portfolio value positive. Returns None when there are fewer
    than two flows, all non-zero flows share one sign, or neither Newton nor
    bisection converges. The result is quantized to 6 decimal places
    (ROUND_HALF_UP).
    """
    if len(flows) < 2:
        return None
    has_positive = any(amount > 0 for _, amount in flows)
    has_negative = any(amount < 0 for _, amount in flows)
    if not (has_positive and has_negative):
        return None
    ordered = sorted(flows, key=lambda flow: flow[0])
    start = ordered[0][0]
    times = [(d - start).days / _DAYS_PER_YEAR for d, _ in ordered]
    amounts = [float(amount) for _, amount in ordered]
    rate = _solve_newton(times, amounts)
    if rate is None:
        rate = _solve_bisection(times, amounts)
    if rate is None or not math.isfinite(rate):
        return None
    return Decimal(repr(rate)).quantize(_SIX_DP, rounding=ROUND_HALF_UP)

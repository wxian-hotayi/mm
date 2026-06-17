"""High-precision Decimal money helpers.

Currency (USD/MYR), share quantities and FX rates are ``decimal.Decimal``
end-to-end. Floats are rejected at construction time to enforce that
discipline; the only sanctioned float boundary is :func:`to_float` for JSON
serialization (and the dimensionless rate internals of the XIRR solver).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from app.core.errors import ValidationFailed

ZERO: Final[Decimal] = Decimal("0")

_QUANTUM_2DP: Final[Decimal] = Decimal("0.01")
_QUANTUM_4DP: Final[Decimal] = Decimal("0.0001")
_QUANTUM_6DP: Final[Decimal] = Decimal("0.000001")
_QUANTUM_8DP: Final[Decimal] = Decimal("0.00000001")


def _ensure_decimal(value: Decimal) -> Decimal:
    """Return ``value`` unchanged if it is a Decimal; raise TypeError otherwise.

    Floats (and bools) raise immediately — they must never reach money math.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError(
            f"Expected decimal.Decimal, got {type(value).__name__}; "
            "floats are not allowed on money paths"
        )
    return value


def D(x: str | int | Decimal) -> Decimal:
    """Construct an exact Decimal from a string, int or Decimal.

    Floats and bools raise ``TypeError`` to enforce the no-float discipline.
    Non-finite values (NaN/Infinity) and unparsable strings raise
    :class:`ValidationFailed`.
    """
    if isinstance(x, bool) or isinstance(x, float):
        raise TypeError(
            f"D() does not accept {type(x).__name__}; pass str, int or "
            "decimal.Decimal to keep money math exact"
        )
    if isinstance(x, Decimal):
        result = x
    elif isinstance(x, (str, int)):
        try:
            result = Decimal(x)
        except InvalidOperation as exc:
            raise ValidationFailed(
                f"Value {x!r} is not a valid decimal number"
            ) from exc
    else:
        raise TypeError(
            f"D() does not accept {type(x).__name__}; pass str, int or "
            "decimal.Decimal"
        )
    if not result.is_finite():
        raise ValidationFailed(
            f"Value {x!r} is not a finite decimal number"
        )
    return result


def Q4(x: Decimal) -> Decimal:
    """Quantize to 4 decimal places, ROUND_HALF_UP (internal money precision)."""
    return _ensure_decimal(x).quantize(_QUANTUM_4DP, rounding=ROUND_HALF_UP)


def Q2(x: Decimal) -> Decimal:
    """Quantize to 2 decimal places, ROUND_HALF_UP (MYR display precision)."""
    return _ensure_decimal(x).quantize(_QUANTUM_2DP, rounding=ROUND_HALF_UP)


def Q6(x: Decimal) -> Decimal:
    """Quantize to 6 decimal places, ROUND_HALF_UP (rate/return precision)."""
    return _ensure_decimal(x).quantize(_QUANTUM_6DP, rounding=ROUND_HALF_UP)


def to_float(x: Decimal) -> float:
    """Convert a Decimal to float for the JSON serialization boundary ONLY.

    Quantizes to 8 decimal places (ROUND_HALF_UP) before conversion. This is
    the single sanctioned place where money values become floats.
    """
    return float(
        _ensure_decimal(x).quantize(_QUANTUM_8DP, rounding=ROUND_HALF_UP)
    )


def safe_div(a: Decimal, b: Decimal) -> Decimal:
    """Divide ``a / b`` as Decimals, raising :class:`ValidationFailed` when
    the denominator is zero. Both operands must be Decimal."""
    _ensure_decimal(a)
    _ensure_decimal(b)
    if b == ZERO:
        raise ValidationFailed("Division by zero in financial calculation")
    return a / b

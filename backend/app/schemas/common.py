"""Shared schema primitives: exact-Decimal money fields and pagination.

Request money values arrive as ``str | int | float`` JSON and are converted
via ``Decimal(str(value))`` quantized to 4 decimal places (ROUND_HALF_UP) —
no float ever participates in arithmetic. Response money values stay Decimal
internally and are emitted as JSON numbers through
:func:`app.utils.money.to_float`, the single sanctioned float boundary.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from app.utils.money import to_float

_QUANTUM_4DP = Decimal("0.0001")


def coerce_decimal_4dp(value: object) -> Decimal:
    """Parse a request value into an exact Decimal quantized to 4dp.

    Accepts ``Decimal``, ``str``, ``int`` and ``float`` (floats go through
    ``str()`` so the shortest-repr literal is parsed exactly, never the
    binary float expansion). Booleans, non-finite values and unparsable
    strings are rejected.
    """
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid decimal amount")
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (str, int, float)):
        literal = str(value).strip()
        if not literal:
            raise ValueError("decimal amount must not be empty")
        try:
            candidate = Decimal(literal)
        except InvalidOperation as exc:
            raise ValueError(
                f"{value!r} is not a valid decimal number"
            ) from exc
    else:
        raise ValueError(
            f"{type(value).__name__} is not a valid decimal amount"
        )
    if not candidate.is_finite():
        raise ValueError("decimal amount must be finite")
    try:
        return candidate.quantize(_QUANTUM_4DP, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(
            f"{value!r} is too large for an 18-digit, 4-decimal amount"
        ) from exc


MoneyIn = Annotated[Decimal, BeforeValidator(coerce_decimal_4dp)]
PositiveMoneyIn = Annotated[
    Decimal, BeforeValidator(coerce_decimal_4dp), Field(gt=0)
]
NonNegativeMoneyIn = Annotated[
    Decimal, BeforeValidator(coerce_decimal_4dp), Field(ge=0)
]
MoneyOut = Annotated[
    Decimal, PlainSerializer(to_float, return_type=float, when_used="json")
]

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    """Standard paginated response envelope."""

    items: list[T]
    total: int
    page: int
    page_size: int

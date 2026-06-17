"""High-precision Money column type.

``Money`` stores exact decimal values: ``NUMERIC(18, 4)`` on PostgreSQL and
TEXT-backed exact decimals on SQLite. Every bind is quantized to 4 decimal
places with ROUND_HALF_UP, and floats are rejected outright — currency,
share and FX values must be :class:`decimal.Decimal` end-to-end.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

MONEY_PRECISION = 18
MONEY_SCALE = 4
MONEY_QUANTUM = Decimal("0.0001")


class Money(TypeDecorator[Decimal]):
    """Exact 4-decimal-place monetary/share/FX column type."""

    impl = Numeric(MONEY_PRECISION, MONEY_SCALE)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric(MONEY_PRECISION, MONEY_SCALE))

    def process_bind_param(
        self, value: Decimal | int | str | None, dialect: Dialect
    ) -> Decimal | str | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                "float values are not allowed for Money columns; "
                "use decimal.Decimal"
            )
        if not isinstance(value, Decimal):
            try:
                value = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError(
                    f"Value {value!r} is not a valid decimal for a Money column"
                ) from exc
        quantized = value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if dialect.name == "sqlite":
            return str(quantized)
        return quantized

    def process_result_value(
        self, value: Any, dialect: Dialect
    ) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

"""Investment Policy Statement — exactly one policy row per user.

``target_weights`` and ``allowed_symbols`` are stored as JSON text; weights
are JSON strings (e.g. ``{"VOO": "0.70", "QQQ": "0.30"}``) so they parse
losslessly into :class:`decimal.Decimal`.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Money

DEFAULT_TARGET_WEIGHTS: Final[str] = '{"VOO": "0.70", "QQQ": "0.30"}'
DEFAULT_ALLOWED_SYMBOLS: Final[str] = '["VOO", "QQQ"]'


class IpsEnforcementLevel(str, enum.Enum):
    """Per-rule enforcement tier (DESIGN §19.5, Decision Log 20).

    ``INFO`` logs only; ``WARN`` surfaces in Action Status and lowers the
    compliance score but allows execution; ``BLOCK`` rejects with 422 and is
    reserved for forbidden asset classes (leverage/options/non-allowed).
    """

    INFO = "INFO"
    WARN = "WARN"
    BLOCK = "BLOCK"


_ENFORCEMENT_VALUES = ", ".join(
    f"'{level.value}'" for level in IpsEnforcementLevel
)


def _enforcement_check(column: str) -> CheckConstraint:
    """Build the ``IN (...)`` check constraint for an enforcement column."""
    return CheckConstraint(
        f"{column} IN ({_ENFORCEMENT_VALUES})", name=column
    )


class IpsRule(Base):
    __tablename__ = "ips_rules"
    __table_args__ = (
        _enforcement_check("enforce_forbidden_assets"),
        _enforcement_check("enforce_leverage"),
        _enforcement_check("enforce_options"),
        _enforcement_check("enforce_drift"),
        _enforcement_check("enforce_min_holding"),
        _enforcement_check("enforce_cash_drag"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    target_weights: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_TARGET_WEIGHTS
    )
    drift_threshold_pct: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("3.0"),
        server_default=text("3.0"),
    )
    rebalance_frequency_months: Mapped[int] = mapped_column(
        nullable=False, default=6, server_default=text("6")
    )
    min_holding_period_years: Mapped[int] = mapped_column(
        nullable=False, default=10, server_default=text("10")
    )
    allowed_symbols: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_ALLOWED_SYMBOLS
    )
    no_individual_stocks: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    no_options: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    no_leverage: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    max_cash_drag_pct: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("5.0"),
        server_default=text("5.0"),
    )
    # --- Three-tier enforcement levels (§19.5, Decision Log 20) ---
    # Forbidden-asset / leverage / options are the only BLOCK-eligible rules.
    enforce_forbidden_assets: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.BLOCK.value,
        server_default=text("'BLOCK'"),
    )
    enforce_leverage: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.BLOCK.value,
        server_default=text("'BLOCK'"),
    )
    enforce_options: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.BLOCK.value,
        server_default=text("'BLOCK'"),
    )
    # Behavioral / policy rules are WARN/INFO, never BLOCK (clamped if set).
    enforce_drift: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.WARN.value,
        server_default=text("'WARN'"),
    )
    enforce_min_holding: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.WARN.value,
        server_default=text("'WARN'"),
    )
    enforce_cash_drag: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=IpsEnforcementLevel.INFO.value,
        server_default=text("'INFO'"),
    )
    # --- Execution-engine config (§19.5, §19.6) ---
    # MYR. Minimum deployable surplus that signals deployment readiness.
    min_deploy_threshold_myr: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("1500"),
        server_default=text("1500"),
    )
    # Days of lead time before a window that triggers REVIEW_REQUIRED.
    review_lead_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=14, server_default=text("14")
    )
    # Calendar month (1-12) anchoring the unified window schedule.
    execution_anchor_month: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    # Months between deployment windows (default quarterly).
    deployment_interval_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    # Months between rebalance windows (must be a multiple of deployment).
    rebalance_interval_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default=text("6")
    )
    # Number of days a window stays open after it opens.
    execution_window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=21, server_default=text("21")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

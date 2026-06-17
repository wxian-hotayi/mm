"""IPS policy + three-tier enforcement schemas (DESIGN §19.5, §19.7).

``IpsRuleOut``/``IpsRuleIn`` expose the stored policy including the per-rule
enforcement levels and the Unified Execution Window config. The JSON-text
columns (``target_weights``, ``allowed_symbols``) are surfaced as structured
data: weights as a ``{symbol: decimal}`` map (0–1 scale) and allowed symbols as
a list. ``ValidateActionIn`` drives ``POST /ips/validate``;
``EnforcementVerdictOut`` mirrors :class:`app.services.ips_enforcement.EnforcementVerdict`;
``ComplianceOut`` carries the score, standing violations and persisted alerts.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ips import IpsEnforcementLevel, IpsRule
from app.schemas.common import MoneyOut, NonNegativeMoneyIn, PositiveMoneyIn
from app.services.ips_enforcement import EnforcementVerdict, IpsViolation

_MAX_SYMBOL_LENGTH = 16


def _parse_weights(raw: str) -> dict[str, Decimal]:
    """Parse the ``target_weights`` JSON text into a ``{symbol: Decimal}`` map."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    weights: dict[str, Decimal] = {}
    for symbol, value in parsed.items():
        try:
            weights[str(symbol).upper()] = Decimal(str(value))
        except Exception:  # pragma: no cover - defensive on corrupt rows
            continue
    return weights


def _parse_symbols(raw: str) -> list[str]:
    """Parse the ``allowed_symbols`` JSON array into an uppercase symbol list."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(symbol).upper() for symbol in parsed if isinstance(symbol, str)]


class IpsRuleOut(BaseModel):
    """The user's IPS policy with enforcement levels and window config (§19.5)."""

    id: int
    target_weights: dict[str, MoneyOut]
    drift_threshold_pct: MoneyOut
    rebalance_frequency_months: int
    min_holding_period_years: int
    allowed_symbols: list[str]
    no_individual_stocks: bool
    no_options: bool
    no_leverage: bool
    max_cash_drag_pct: MoneyOut
    # Three-tier enforcement levels (§19.5).
    enforce_forbidden_assets: str
    enforce_leverage: str
    enforce_options: str
    enforce_drift: str
    enforce_min_holding: str
    enforce_cash_drag: str
    # Execution-engine config (§19.5, §19.6).
    min_deploy_threshold_myr: MoneyOut
    review_lead_days: int
    execution_anchor_month: int
    deployment_interval_months: int
    rebalance_interval_months: int
    execution_window_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, ips: IpsRule) -> "IpsRuleOut":
        return cls(
            id=ips.id,
            target_weights=_parse_weights(ips.target_weights),
            drift_threshold_pct=ips.drift_threshold_pct,
            rebalance_frequency_months=ips.rebalance_frequency_months,
            min_holding_period_years=ips.min_holding_period_years,
            allowed_symbols=_parse_symbols(ips.allowed_symbols),
            no_individual_stocks=ips.no_individual_stocks,
            no_options=ips.no_options,
            no_leverage=ips.no_leverage,
            max_cash_drag_pct=ips.max_cash_drag_pct,
            enforce_forbidden_assets=ips.enforce_forbidden_assets,
            enforce_leverage=ips.enforce_leverage,
            enforce_options=ips.enforce_options,
            enforce_drift=ips.enforce_drift,
            enforce_min_holding=ips.enforce_min_holding,
            enforce_cash_drag=ips.enforce_cash_drag,
            min_deploy_threshold_myr=ips.min_deploy_threshold_myr,
            review_lead_days=ips.review_lead_days,
            execution_anchor_month=ips.execution_anchor_month,
            deployment_interval_months=ips.deployment_interval_months,
            rebalance_interval_months=ips.rebalance_interval_months,
            execution_window_days=ips.execution_window_days,
            is_active=ips.is_active,
            created_at=ips.created_at,
            updated_at=ips.updated_at,
        )


class IpsRuleIn(BaseModel):
    """Partial update for the IPS policy (``PUT /ips``); unset fields keep value.

    Enforcement-level fields accept INFO/WARN/BLOCK; the service clamps
    behavioral rules (drift/min-holding/cash-drag) to at most WARN so policy
    can never hard-block the user's own ledger (§19.5). Window-config integers
    are validated for consistency by the execution engine on use.
    """

    model_config = ConfigDict(extra="forbid")

    target_weights: dict[str, NonNegativeMoneyIn] | None = None
    drift_threshold_pct: PositiveMoneyIn | None = None
    rebalance_frequency_months: int | None = Field(default=None, ge=1, le=120)
    min_holding_period_years: int | None = Field(default=None, ge=0, le=100)
    allowed_symbols: list[str] | None = None
    no_individual_stocks: bool | None = None
    no_options: bool | None = None
    no_leverage: bool | None = None
    max_cash_drag_pct: NonNegativeMoneyIn | None = None
    enforce_forbidden_assets: IpsEnforcementLevel | None = None
    enforce_leverage: IpsEnforcementLevel | None = None
    enforce_options: IpsEnforcementLevel | None = None
    enforce_drift: IpsEnforcementLevel | None = None
    enforce_min_holding: IpsEnforcementLevel | None = None
    enforce_cash_drag: IpsEnforcementLevel | None = None
    min_deploy_threshold_myr: NonNegativeMoneyIn | None = None
    review_lead_days: int | None = Field(default=None, ge=0, le=365)
    execution_anchor_month: int | None = Field(default=None, ge=1, le=12)
    deployment_interval_months: int | None = Field(default=None, ge=1, le=120)
    rebalance_interval_months: int | None = Field(default=None, ge=1, le=120)
    execution_window_days: int | None = Field(default=None, ge=1, le=366)
    is_active: bool | None = None

    @field_validator("allowed_symbols", mode="after")
    @classmethod
    def _clean_symbols(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for symbol in value:
            sym = symbol.strip().upper()
            if not sym:
                raise ValueError("allowed_symbols must not contain blanks")
            if len(sym) > _MAX_SYMBOL_LENGTH:
                raise ValueError(
                    f"symbol {sym!r} exceeds {_MAX_SYMBOL_LENGTH} characters"
                )
            if sym not in cleaned:
                cleaned.append(sym)
        return cleaned

    @field_validator("target_weights", mode="after")
    @classmethod
    def _clean_weights(
        cls, value: dict[str, Decimal] | None
    ) -> dict[str, Decimal] | None:
        if value is None:
            return None
        cleaned: dict[str, Decimal] = {}
        for symbol, weight in value.items():
            sym = symbol.strip().upper()
            if not sym:
                raise ValueError("target_weights keys must not be blank")
            cleaned[sym] = weight
        return cleaned


class ValidateActionIn(BaseModel):
    """Request for ``POST /ips/validate`` — an action to run through the gate.

    ``kind`` defaults to ``TRANSACTION``. For a transaction supply ``type``
    (BUY/SELL/...) and a symbol (``asset_symbol``/``symbol``/``ticker``); for an
    ``EXECUTION_PLAN`` / ``AI_RECOMMENDATION`` supply ``orders`` (each a mapping
    with a symbol + side). ``override`` requests the audited bypass of a BLOCK.
    """

    model_config = ConfigDict(extra="allow")

    kind: str = "TRANSACTION"
    override: bool = False


class IpsViolationOut(BaseModel):
    """One IPS violation (mirrors :class:`IpsViolation`)."""

    model_config = ConfigDict(from_attributes=True)

    rule_type: str
    level: str
    message: str
    evidence: dict[str, Any]

    @classmethod
    def from_violation(cls, violation: IpsViolation) -> "IpsViolationOut":
        return cls.model_validate(violation)


class EnforcementVerdictOut(BaseModel):
    """Verdict for a validated action (mirrors :class:`EnforcementVerdict`).

    ``allowed`` is False iff a BLOCK violation exists and ``override`` was not
    set. ``violations`` are the BLOCK-level entries; ``warnings`` are WARN/INFO
    (which never block).
    """

    allowed: bool
    max_level: str | None
    violations: list[IpsViolationOut]
    warnings: list[IpsViolationOut]

    @classmethod
    def from_verdict(cls, verdict: EnforcementVerdict) -> "EnforcementVerdictOut":
        return cls(
            allowed=verdict.allowed,
            max_level=verdict.max_level,
            violations=[
                IpsViolationOut.from_violation(v) for v in verdict.violations
            ],
            warnings=[
                IpsViolationOut.from_violation(v) for v in verdict.warnings
            ],
        )


class ComplianceOut(BaseModel):
    """IPS compliance report (``GET /ips/compliance``): score + violations + alerts."""

    score: int
    violations: list[IpsViolationOut]
    alerts: list[IpsViolationOut]


class IpsPolicyOut(BaseModel):
    """``GET`` / ``PUT /ips`` response: the rules plus live compliance."""

    rules: IpsRuleOut
    compliance: ComplianceOut

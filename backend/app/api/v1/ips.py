"""IPS policy + three-tier enforcement endpoints (DESIGN §19.5, §19.7).

``GET``/``PUT /ips`` read and update the stored policy (including the per-rule
enforcement levels and the unified-window config); ``POST /ips/validate`` runs
an action through the enforcement gate; ``GET /ips/compliance`` returns the
score, standing violations and persisted alerts. JWT-guarded, per-user.

Behavioral rules (drift / min-holding / cash-drag) are clamped to at most WARN
on write — policy can never hard-block the user's own ledger (§19.5). The
forbidden-asset / leverage / options rules are the only BLOCK-eligible ones.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, DbDep
from app.core.errors import NotFoundError
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.ips import IpsEnforcementLevel, IpsRule
from app.models.user import User
from app.schemas.ips import (
    ComplianceOut,
    EnforcementVerdictOut,
    IpsPolicyOut,
    IpsRuleIn,
    IpsRuleOut,
    IpsViolationOut,
    ValidateActionIn,
)
from app.services import execution, ips_enforcement
from app.utils.money import D

router = APIRouter(prefix="/ips", tags=["ips"])

# Behavioral rules whose enforcement level is clamped to at most WARN (§19.5).
_BEHAVIORAL_LEVEL_FIELDS = (
    "enforce_drift",
    "enforce_min_holding",
    "enforce_cash_drag",
)


async def _load_ips(db: AsyncSession, user: User) -> IpsRule:
    """Load the user's single IPS policy row; raise NotFoundError otherwise."""
    result = await db.execute(select(IpsRule).where(IpsRule.user_id == user.id))
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError("No Investment Policy Statement found for this user")
    return ips


def _prices_from_query(raw: list[str] | None) -> dict[str, Decimal] | None:
    """Parse repeated ``price=SYMBOL:VALUE`` query params into a price map."""
    if not raw:
        return None
    prices: dict[str, Decimal] = {}
    for item in raw:
        symbol, _, value = item.partition(":")
        symbol = symbol.strip().upper()
        if not symbol or not value.strip():
            continue
        prices[symbol] = D(value.strip())
    return prices or None


def _clamp_behavioral(level: str) -> str:
    """Clamp a behavioral-rule level to at most WARN (BLOCK -> WARN, §19.5)."""
    if level == IpsEnforcementLevel.BLOCK.value:
        return IpsEnforcementLevel.WARN.value
    return level


async def _compliance(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None,
    fx_rate: Decimal | None,
) -> ComplianceOut:
    """Build the compliance report (score + standing violations + alerts)."""
    score = await ips_enforcement.compute_compliance_score(
        db, user, prices=prices, fx_rate=fx_rate
    )
    alerts = await ips_enforcement.alerts(
        db, user, prices=prices, fx_rate=fx_rate
    )
    violations = [IpsViolationOut.from_violation(v) for v in alerts]
    return ComplianceOut(score=score, violations=violations, alerts=violations)


@router.get("", response_model=IpsPolicyOut)
async def get_ips(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
) -> IpsPolicyOut:
    """Return the user's IPS policy (incl. enforcement levels) + live compliance.

    Supply ``price=SYMBOL:USD`` params and ``fx_rate`` to include drift /
    cash-drag in the compliance figures (asset-class breaches always count)."""
    ips = await _load_ips(db, user)
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    compliance = await _compliance(db, user, prices, fx)
    return IpsPolicyOut(rules=IpsRuleOut.from_row(ips), compliance=compliance)


@router.put("", response_model=IpsPolicyOut)
async def update_ips(
    payload: IpsRuleIn, db: DbDep, user: CurrentUser
) -> IpsPolicyOut:
    """Update the IPS policy (rules, enforcement levels, window config).

    Only supplied fields change. Behavioral enforcement levels set to BLOCK are
    clamped to WARN on write (§19.5). The mutation is audited."""
    ips = await _load_ips(db, user)
    data = payload.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}

    if "target_weights" in data and data["target_weights"] is not None:
        weights = {
            symbol: str(weight)
            for symbol, weight in data.pop("target_weights").items()
        }
        ips.target_weights = json.dumps(weights)
        changes["target_weights"] = weights
    else:
        data.pop("target_weights", None)

    if "allowed_symbols" in data and data["allowed_symbols"] is not None:
        symbols = list(data.pop("allowed_symbols"))
        ips.allowed_symbols = json.dumps(symbols)
        changes["allowed_symbols"] = symbols
    else:
        data.pop("allowed_symbols", None)

    for field, value in data.items():
        if value is None:
            continue
        if isinstance(value, IpsEnforcementLevel):
            value = value.value
        if field in _BEHAVIORAL_LEVEL_FIELDS:
            value = _clamp_behavioral(value)
        setattr(ips, field, value)
        changes[field] = value

    # Validate the unified-window config against the EFFECTIVE (post-merge)
    # values so a partial update can never persist an inconsistent schedule
    # (e.g. rebalance_interval not a multiple of deployment_interval), which
    # would otherwise break every cycle / action-status / execution read for
    # this user with a 422 at read time (§19.6, DL 19). Reuse the single
    # validator owned by the execution engine.
    execution._validate_config(ips)  # noqa: SLF001 - single source of truth

    db.add(
        AuditLog(
            user_id=user.id,
            event_type=AuditEventType.AUDIT.value,
            action="IPS_UPDATE",
            severity=AuditSeverity.INFO.value,
            entity="ips",
            entity_id=str(ips.id),
            description="Updated IPS policy",
            context=json.dumps(changes, default=str),
        )
    )
    await db.commit()
    await db.refresh(ips)
    compliance = await _compliance(db, user, None, None)
    return IpsPolicyOut(rules=IpsRuleOut.from_row(ips), compliance=compliance)


@router.post("/validate", response_model=EnforcementVerdictOut)
async def validate_action(
    payload: ValidateActionIn, db: DbDep, user: CurrentUser
) -> EnforcementVerdictOut:
    """Run an action through the IPS enforcement gate (§19.5).

    Returns an :class:`EnforcementVerdict`: ``allowed`` is False iff a
    BLOCK-level violation exists and ``override`` was not set. This is a pure
    read — it never persists a transaction."""
    action = payload.model_dump()
    override = bool(action.pop("override", False))
    verdict = await ips_enforcement.validate_action(
        db, user, action, override=override
    )
    return EnforcementVerdictOut.from_verdict(verdict)


@router.get("/compliance", response_model=ComplianceOut)
async def ips_compliance(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
) -> ComplianceOut:
    """Return the IPS compliance score, standing violations and alerts (§19.5)."""
    await _load_ips(db, user)
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    return await _compliance(db, user, prices, fx)

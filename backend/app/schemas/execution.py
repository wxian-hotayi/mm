"""Execution Window + plan schemas (DESIGN §19.6, §19.7).

``ExecutionPlanIn`` drives ``POST /execution/plan`` (prices + USD->MYR FX, with
an optional ``kind``). ``ExecutionPlanOut`` mirrors the stored
:class:`~app.models.execution.ExecutionPlan`, decoding its JSON columns
(allocation snapshots, orders, steps, IPS violations) for the response.
``WindowsOut`` reports the unified schedule from
:func:`app.services.execution.classify_window`.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.execution import ExecutionPlan, ExecutionPlanKind
from app.schemas.common import MoneyOut, PositiveMoneyIn
from app.schemas.portfolio import _normalize_price_keys


def _decode_json(raw: str, fallback: Any) -> Any:
    """Best-effort decode of a JSON-text column; returns ``fallback`` on error."""
    try:
        return json.loads(raw) if raw else fallback
    except json.JSONDecodeError:  # pragma: no cover - corrupt row guard
        return fallback


class ExecutionPlanIn(BaseModel):
    """Request for ``POST /execution/plan`` — generate a DRAFT plan.

    ``prices`` are USD per share for every held and target symbol; ``fx_rate``
    is the USD->MYR rate. ``kind`` (DEPLOY / REBALANCE / DEPLOY_AND_REBALANCE)
    overrides the window-derived default; omit it to default from
    ``classify_window(today)``.
    """

    model_config = ConfigDict(extra="forbid")

    prices: dict[str, PositiveMoneyIn]
    fx_rate: PositiveMoneyIn
    kind: ExecutionPlanKind | None = None

    @field_validator("prices", mode="after")
    @classmethod
    def _symbols(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        return _normalize_price_keys(value)


class ExecutionPlanApproveIn(BaseModel):
    """Optional body for ``POST /execution/plans/{id}/approve``.

    ``override=True`` is the audited bypass of a BLOCK-level IPS violation
    (§19.5); without it a BLOCK rejects the approval with HTTP 422.
    """

    model_config = ConfigDict(extra="forbid")

    override: bool = False


class ExecutionPlanOut(BaseModel):
    """A stored execution plan with its JSON columns decoded (§19.6)."""

    id: int
    window_date: date
    plan_kind: str
    status: str
    cash_deployed_myr: MoneyOut
    cash_deployed_usd: MoneyOut
    fx_rate_used: MoneyOut | None
    allocation_before: dict[str, Any]
    allocation_after: dict[str, Any]
    orders: list[dict[str, Any]]
    steps: list[str]
    ips_compliant: bool
    ips_violations: list[dict[str, Any]]
    created_at: datetime
    executed_at: datetime | None

    @classmethod
    def from_row(cls, plan: ExecutionPlan) -> "ExecutionPlanOut":
        return cls(
            id=plan.id,
            window_date=plan.window_date,
            plan_kind=plan.plan_kind,
            status=plan.status,
            cash_deployed_myr=plan.cash_deployed_myr,
            cash_deployed_usd=plan.cash_deployed_usd,
            fx_rate_used=plan.fx_rate_used,
            allocation_before=_decode_json(plan.allocation_before, {}),
            allocation_after=_decode_json(plan.allocation_after, {}),
            orders=_decode_json(plan.orders, []),
            steps=_decode_json(plan.steps, []),
            ips_compliant=plan.ips_compliant,
            ips_violations=_decode_json(plan.ips_violations, []),
            created_at=plan.created_at,
            executed_at=plan.executed_at,
        )


class WindowScheduleItem(BaseModel):
    """One upcoming execution window in the schedule preview."""

    open_date: date
    kind: str


class WindowsOut(BaseModel):
    """The unified execution-window schedule (``GET /execution/windows``, §19.6).

    ``open_window`` / ``open_window_kind`` describe today's open window (if any);
    ``next_window_date`` / ``is_rebalance`` describe the next opening; ``schedule``
    lists upcoming windows.
    """

    today: date
    open_window: bool
    open_window_kind: str | None
    next_window_date: date
    next_window_kind: str
    is_rebalance: bool
    schedule: list[WindowScheduleItem]

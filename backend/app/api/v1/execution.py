"""Execution Window + plan endpoints (DESIGN §19.6, §19.7).

Exposes the single unified execution-window schedule and the execution-plan
lifecycle (generate DRAFT -> approve -> execute / skip). JWT-guarded, per-user;
mutations are audited in the service. Plan generation and approval run through
the IPS enforcement gate — a BLOCK without ``override`` rejects with 422.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, DbDep
from app.core.errors import NotFoundError
from app.models.execution import ExecutionPlanStatus
from app.models.ips import IpsRule
from app.models.user import User
from app.schemas.execution import (
    ExecutionPlanApproveIn,
    ExecutionPlanIn,
    ExecutionPlanOut,
    WindowScheduleItem,
    WindowsOut,
)
from app.services import execution
from app.utils.dates import kl_today

router = APIRouter(prefix="/execution", tags=["execution"])

# How many upcoming windows to include in the schedule preview.
_SCHEDULE_PREVIEW_COUNT = 8
_ONE_DAY = timedelta(days=1)


async def _load_ips(db: AsyncSession, user: User) -> IpsRule:
    """Load the user's single IPS policy row; raise NotFoundError otherwise."""
    result = await db.execute(select(IpsRule).where(IpsRule.user_id == user.id))
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError("No Investment Policy Statement found for this user")
    return ips


def _build_schedule(ips: IpsRule, today: date) -> list[WindowScheduleItem]:
    """Build the next ``_SCHEDULE_PREVIEW_COUNT`` upcoming windows (open, kind).

    Walks the single unified schedule via :func:`execution.next_window`,
    stepping past each window's close so successive openings are returned.
    """
    schedule: list[WindowScheduleItem] = []
    cursor = today
    for _ in range(_SCHEDULE_PREVIEW_COUNT):
        open_date, kind = execution.next_window(cursor, ips)
        schedule.append(WindowScheduleItem(open_date=open_date, kind=kind))
        window = execution.current_open_window(open_date, ips)
        step_from = window.closes if window is not None else open_date
        cursor = step_from + _ONE_DAY
    return schedule


@router.get("/windows", response_model=WindowsOut)
async def execution_windows(
    db: DbDep,
    user: CurrentUser,
    as_of: Annotated[date | None, Query()] = None,
) -> WindowsOut:
    """Return the unified execution-window schedule (§19.6): the open window (if
    any), the next opening + whether it is a rebalance window, and a preview of
    upcoming windows."""
    ips = await _load_ips(db, user)
    today = as_of if as_of is not None else kl_today()
    open_kind = execution.classify_window(today, ips)
    # The next opening is the next FUTURE window, never the current open
    # window's past anchor (§19.7).
    next_date, next_kind = execution.next_future_window(today, ips)
    return WindowsOut(
        today=today,
        open_window=open_kind is not None,
        open_window_kind=open_kind,
        next_window_date=next_date,
        next_window_kind=next_kind,
        is_rebalance=next_kind == execution.WINDOW_REBALANCE,
        schedule=_build_schedule(ips, today),
    )


@router.post(
    "/plan",
    response_model=ExecutionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_plan(
    payload: ExecutionPlanIn, db: DbDep, user: CurrentUser
) -> ExecutionPlanOut:
    """Generate and persist a DRAFT execution plan for the current window (§19.6).

    ``prices`` (USD per held/target symbol) and ``fx_rate`` (USD->MYR) price the
    plan; ``kind`` defaults from ``classify_window(today)``. The plan is run
    through the IPS gate and flagged accordingly."""
    plan = await execution.generate_execution_plan(
        db, user, payload.prices, payload.fx_rate, kind=payload.kind
    )
    return ExecutionPlanOut.from_row(plan)


@router.get("/plans", response_model=list[ExecutionPlanOut])
async def list_plans(
    db: DbDep,
    user: CurrentUser,
    plan_status: Annotated[
        ExecutionPlanStatus | None, Query(alias="status")
    ] = None,
) -> list[ExecutionPlanOut]:
    """List the user's execution plans (newest first), optional status filter."""
    plans = await execution.list_plans(db, user, plan_status)
    return [ExecutionPlanOut.from_row(plan) for plan in plans]


@router.get("/plans/{plan_id}", response_model=ExecutionPlanOut)
async def get_plan(
    plan_id: int, db: DbDep, user: CurrentUser
) -> ExecutionPlanOut:
    """Fetch one of the user's execution plans by id."""
    plan = await execution.get_plan(db, user, plan_id)
    return ExecutionPlanOut.from_row(plan)


@router.post("/plans/{plan_id}/approve", response_model=ExecutionPlanOut)
async def approve_plan(
    plan_id: int,
    db: DbDep,
    user: CurrentUser,
    payload: Annotated[ExecutionPlanApproveIn | None, Body()] = None,
) -> ExecutionPlanOut:
    """Approve a DRAFT plan after re-validating it through IPS enforcement.

    A BLOCK-level violation without ``override`` rejects with HTTP 422 (audited
    as a critical IPS_ALERT); an audited ``override=True`` is the sole bypass."""
    body = payload or ExecutionPlanApproveIn()
    plan = await execution.approve(db, user, plan_id, override=body.override)
    return ExecutionPlanOut.from_row(plan)


@router.post("/plans/{plan_id}/execute", response_model=ExecutionPlanOut)
async def execute_plan(
    plan_id: int, db: DbDep, user: CurrentUser
) -> ExecutionPlanOut:
    """Mark an APPROVED execution plan EXECUTED (sets ``executed_at``)."""
    plan = await execution.execute(db, user, plan_id)
    return ExecutionPlanOut.from_row(plan)


@router.post("/plans/{plan_id}/skip", response_model=ExecutionPlanOut)
async def skip_plan(
    plan_id: int, db: DbDep, user: CurrentUser
) -> ExecutionPlanOut:
    """Mark a DRAFT/APPROVED execution plan SKIPPED (discipline-preserving)."""
    plan = await execution.skip(db, user, plan_id)
    return ExecutionPlanOut.from_row(plan)

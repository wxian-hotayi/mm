"""Unified Execution Window Engine + plan generation (DESIGN §19.6, DL 19).

There is **exactly one** scheduler in WealthOS — :func:`classify_window`. It is
the single source of truth used by the Wealth Operating Cycle (§19.2) and the
Action Status engine (§19.3); neither of those engines may compute windows on
their own. A ``DEPLOYMENT_WINDOW`` opens on day 1 of every month ``M`` where
``(M − execution_anchor_month) mod deployment_interval_months == 0`` and stays
open ``execution_window_days``; such a window is *also* a ``REBALANCE_WINDOW``
when ``(M − execution_anchor_month) mod rebalance_interval_months ==
(rebalance_interval_months − deployment_interval_months)``. By construction
**REBALANCE ⊂ DEPLOYMENT** (defaults → deploy Mar/Jun/Sep/Dec, rebalance
Jun/Dec).

Plan generation (:func:`generate_execution_plan`) turns an open window into a
concrete, IPS-validated :class:`~app.models.execution.ExecutionPlan` with exact
4dp share quantities, built on the Phase-1 rebalance engine:

* **DEPLOY** windows deploy the deployable surplus (÷ FX → USD) toward 70/30
  via the CASH_ONLY path — buys only, floored, never overspending.
* **REBALANCE** / **DEPLOY_AND_REBALANCE** windows run the full Phase-1
  rebalance (cash → contribution → sell-last) allocation correction.

Everything is async, ledger-first (the portfolio stays derived; a plan only
affects holdings once its orders are recorded as transactions), pure-Decimal
and per-user isolated. All dates are Asia/Kuala_Lumpur (``utils.dates``).

Units: ``*_myr`` are MYR, ``*_usd`` are USD, weights are percentages on the
0–100 scale, share quantities are 4dp. Floats never touch any value here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.execution import (
    ExecutionPlan,
    ExecutionPlanKind,
    ExecutionPlanStatus,
)
from app.models.ips import IpsEnforcementLevel, IpsRule
from app.models.transaction import Transaction
from app.models.user import User
from app.services import deployment, ips_enforcement
from app.services.deployment import DeploymentTrigger
from app.services.ledger import LedgerState, replay
from app.services.rebalance import RebalancePlan, RebalanceStatus, plan_rebalance
from app.services.valuation import valuation
from app.utils.dates import kl_today
from app.utils.money import Q2, Q4, ZERO, safe_div

# --- Window kinds returned by classify_window -----------------------------
WINDOW_DEPLOYMENT: Final[str] = "DEPLOYMENT"
WINDOW_REBALANCE: Final[str] = "REBALANCE"

#: ``classify_window`` result: ``None`` (no window), ``"DEPLOYMENT"`` (deploy
#: only) or ``"REBALANCE"`` (deploy *and* rebalance — a superset window).
WindowKind = Literal["DEPLOYMENT", "REBALANCE"]

# How far ahead next_window scans for the next opening window (covers any
# valid interval; deployment intervals are small positive integers).
_NEXT_WINDOW_SCAN_MONTHS: Final[int] = 240

# Rounding slack (USD) tolerated when asserting a deploy-only plan stays within
# its deployable budget (4dp share quantization can leave sub-cent residue).
_DEPLOY_OVERSPEND_TOLERANCE: Final[Decimal] = Decimal("0.01")

# Fixed epoch month index for the window scheduler's modular arithmetic so the
# cadence is an absolute count from one anchor, never reset each calendar year
# (DESIGN §19.6 — intervals need not divide 12). Year 1 keeps the index small.
_EPOCH_ANCHOR_YEAR: Final[int] = 1


@dataclass(frozen=True)
class OpenWindow:
    """The currently open execution window (from :func:`current_open_window`).

    ``anchor_date`` is the day the window opened (day 1 of its month);
    ``opens`` == ``anchor_date``; ``closes`` is the inclusive last day
    (``opens + execution_window_days − 1``). ``kind`` is ``"DEPLOYMENT"`` or
    ``"REBALANCE"``.
    """

    anchor_date: date
    kind: WindowKind
    opens: date
    closes: date


# --------------------------------------------------------------------------- #
# Config validation                                                            #
# --------------------------------------------------------------------------- #
def _validate_config(ips: IpsRule) -> tuple[int, int, int, int]:
    """Validate and return the window config from an IPS policy.

    Returns ``(anchor_month, deployment_interval, rebalance_interval,
    window_days)``. Raises :class:`ValidationFailed` when the intervals are
    not positive, the window length is not positive, the anchor month is out
    of ``1..12``, or ``rebalance_interval_months`` is not a positive integer
    multiple of ``deployment_interval_months`` (DESIGN §19.6, DL 19).
    """
    anchor = ips.execution_anchor_month
    deploy_interval = ips.deployment_interval_months
    rebalance_interval = ips.rebalance_interval_months
    window_days = ips.execution_window_days
    if not 1 <= anchor <= 12:
        raise ValidationFailed(
            f"execution_anchor_month must be 1-12 (got {anchor})"
        )
    if deploy_interval <= 0:
        raise ValidationFailed(
            "deployment_interval_months must be positive "
            f"(got {deploy_interval})"
        )
    if rebalance_interval <= 0:
        raise ValidationFailed(
            "rebalance_interval_months must be positive "
            f"(got {rebalance_interval})"
        )
    if rebalance_interval % deploy_interval != 0:
        raise ValidationFailed(
            "rebalance_interval_months must be a positive integer multiple of "
            f"deployment_interval_months (got {rebalance_interval} vs "
            f"{deploy_interval})"
        )
    if window_days <= 0:
        raise ValidationFailed(
            f"execution_window_days must be positive (got {window_days})"
        )
    return anchor, deploy_interval, rebalance_interval, window_days


def _month_index(d: date) -> int:
    """Absolute month index (year*12 + month-1) for modular window math."""
    return d.year * 12 + (d.month - 1)


def _epoch_anchor_index(anchor_month: int) -> int:
    """Fixed-epoch month index of ``anchor_month`` (DESIGN §19.6).

    The cadence is measured as an absolute month count from a single fixed
    epoch so it is NEVER reset each calendar year. This keeps intervals that do
    not divide 12 (e.g. 5-monthly) correctly spaced; the default 3/6 intervals
    are unaffected because they divide 12.
    """
    return _EPOCH_ANCHOR_YEAR * 12 + (anchor_month - 1)


# --------------------------------------------------------------------------- #
# THE single scheduler                                                         #
# --------------------------------------------------------------------------- #
def classify_window(when: date, ips: IpsRule) -> WindowKind | None:
    """Classify ``when`` against the single unified window schedule (§19.6).

    Returns:

    * ``"REBALANCE"`` — ``when`` is inside a deployment window that is *also*
      a rebalance window (deploy + sells/correction permitted),
    * ``"DEPLOYMENT"`` — ``when`` is inside a deploy-only window,
    * ``None`` — ``when`` is outside every window.

    A ``DEPLOYMENT_WINDOW`` opens on day 1 of every month ``M`` where
    ``(M − execution_anchor_month) mod deployment_interval_months == 0`` and
    stays open ``execution_window_days``. It is additionally a
    ``REBALANCE_WINDOW`` when ``(M − execution_anchor_month) mod
    rebalance_interval_months == (rebalance_interval_months −
    deployment_interval_months)`` — i.e. every ``rebalance/deploy``-th
    deployment window. **REBALANCE ⊂ DEPLOYMENT** by construction.

    This is the only function that computes windows; the cycle and
    action-status engines call the helpers below, never their own arithmetic.
    Raises :class:`ValidationFailed` on an invalid config.
    """
    anchor, deploy_interval, rebalance_interval, window_days = _validate_config(
        ips
    )
    # A window opens on day 1 of an aligned month and stays open
    # window_days (≤ the interval span in practice). We therefore only need to
    # test the window that could have opened on day 1 of `when`'s own month and
    # — for short months / long windows — the previous aligned month.
    for candidate in _candidate_open_dates(when, anchor, deploy_interval, window_days):
        close_date = candidate + timedelta(days=window_days - 1)
        if not (candidate <= when <= close_date):
            continue
        months_since_anchor = _month_index(candidate) - _epoch_anchor_index(
            anchor
        )
        # months_since_anchor is a multiple of deploy_interval here (candidate
        # is an aligned open month). It is a rebalance window when it is the
        # 2nd (general: rebalance/deploy-th) window of each rebalance cycle.
        if (
            months_since_anchor % rebalance_interval
            == (rebalance_interval - deploy_interval) % rebalance_interval
        ):
            return WINDOW_REBALANCE
        return WINDOW_DEPLOYMENT
    return None


def _candidate_open_dates(
    when: date, anchor_month: int, deploy_interval: int, window_days: int
) -> list[date]:
    """Aligned window-open dates that could still be open on ``when``.

    A window opens on day 1 of an aligned month and stays open ``window_days``;
    so any window open on ``when`` opened either this month or in a recent
    earlier month. We walk back enough months to cover the window length, only
    keeping months whose offset from the anchor is a deploy-interval multiple.
    """
    months_back = window_days // 28 + 1
    candidates: list[date] = []
    base = _month_index(when)
    for back in range(months_back + 1):
        idx = base - back
        year, month_zero = divmod(idx, 12)
        month = month_zero + 1
        open_date = date(year, month, 1)
        if open_date > when:
            continue
        offset = idx - _epoch_anchor_index(anchor_month)
        if offset % deploy_interval == 0:
            candidates.append(open_date)
    return candidates


# --------------------------------------------------------------------------- #
# Helpers — all derived ONLY from classify_window                              #
# --------------------------------------------------------------------------- #
def is_rebalance(when: date, ips: IpsRule) -> bool:
    """True when ``when`` falls in a REBALANCE window (also a deploy window)."""
    return classify_window(when, ips) == WINDOW_REBALANCE


def current_open_window(today: date, ips: IpsRule) -> OpenWindow | None:
    """Return the execution window currently open at ``today`` (or ``None``).

    Derived from :func:`classify_window`: finds the aligned open-date of the
    window containing ``today`` and reports its kind, open and (inclusive)
    close dates. Pure; no database access.
    """
    kind = classify_window(today, ips)
    if kind is None:
        return None
    _anchor, _deploy, _rebalance, window_days = _validate_config(ips)
    anchor = ips.execution_anchor_month
    deploy_interval = ips.deployment_interval_months
    for candidate in _candidate_open_dates(
        today, anchor, deploy_interval, window_days
    ):
        close_date = candidate + timedelta(days=window_days - 1)
        if candidate <= today <= close_date:
            return OpenWindow(
                anchor_date=candidate,
                kind=kind,
                opens=candidate,
                closes=close_date,
            )
    # Unreachable: classify_window returned a kind, so a candidate matched.
    return None  # pragma: no cover


def _scan_open_date_after(
    floor: date, ips: IpsRule
) -> tuple[date, WindowKind]:
    """First aligned window opening on or after ``floor`` (pure forward scan).

    Walks aligned open-dates from ``floor``'s month forward and classifies each
    with :func:`classify_window`. Unlike :func:`next_window` this never reports
    a currently-open window's past anchor — it only returns an opening date that
    is itself ``>= floor``. Raises :class:`ValidationFailed` if none is found
    within the scan horizon (pathological config only).
    """
    anchor, deploy_interval, _rebalance, _window_days = _validate_config(ips)
    base = _month_index(floor)
    for ahead in range(_NEXT_WINDOW_SCAN_MONTHS + 1):
        idx = base + ahead
        year, month_zero = divmod(idx, 12)
        month = month_zero + 1
        open_date = date(year, month, 1)
        if open_date < floor:
            continue
        offset = idx - _epoch_anchor_index(anchor)
        if offset % deploy_interval != 0:
            continue
        kind = classify_window(open_date, ips)
        if kind is not None:
            return open_date, kind
    raise ValidationFailed(
        "No execution window found within the scheduling horizon; check the "
        "window configuration"
    )


def next_window(today: date, ips: IpsRule) -> tuple[date, WindowKind]:
    """Return the next window's ``(open_date, kind)`` on or after ``today``.

    Scans forward over aligned open-dates and classifies each with
    :func:`classify_window` (so the kind always agrees with the scheduler). If
    ``today`` is itself inside a window, that window's open date is returned
    (kept for the cycle's overdue-rebalance scan, which steps cursor by close).
    For the *next future opening* (never a past anchor) use
    :func:`next_future_window`. Raises :class:`ValidationFailed` if no window is
    found within the scan horizon (only possible with a pathological config).
    """
    # If we're inside a window now, report it.
    open_now = current_open_window(today, ips)
    if open_now is not None:
        return open_now.opens, open_now.kind
    return _scan_open_date_after(today, ips)


def next_future_window(today: date, ips: IpsRule) -> tuple[date, WindowKind]:
    """Return the next window opening strictly in the future of ``today``.

    Unlike :func:`next_window`, when ``today`` is inside an open window this
    returns the NEXT window that opens *after* the current one closes — never
    the current window's past anchor. This is the value the API/Action-Status
    contract calls ``next_window_date`` (the next opening, §19.3/§19.7) and the
    basis for the WINDOW_SOON lead-time trigger. Outside any window it agrees
    with :func:`next_window`.
    """
    open_now = current_open_window(today, ips)
    floor = open_now.closes + timedelta(days=1) if open_now is not None else today
    return _scan_open_date_after(floor, ips)


# --------------------------------------------------------------------------- #
# Loading helpers                                                              #
# --------------------------------------------------------------------------- #
async def _load_ips(db: AsyncSession, user: User) -> IpsRule:
    """Load the user's single IPS policy row; raise NotFoundError otherwise."""
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(
            "No Investment Policy Statement found for this user"
        )
    return ips


async def _load_transactions(
    db: AsyncSession, user: User
) -> list[Transaction]:
    """Load all of the user's transactions (per-user isolated)."""
    result = await db.execute(
        select(Transaction).where(Transaction.user_id == user.id)
    )
    return list(result.scalars().all())


def _audit(
    user_id: int,
    action: str,
    entity_id: int | None,
    description: str,
    context: dict[str, object],
) -> AuditLog:
    """Build an INFO ``AUDIT`` log row for an execution-plan mutation."""
    return AuditLog(
        user_id=user_id,
        event_type=AuditEventType.AUDIT.value,
        action=action,
        severity=AuditSeverity.INFO.value,
        entity="execution_plan",
        entity_id=str(entity_id) if entity_id is not None else None,
        description=description,
        context=json.dumps(context, default=str),
    )


# --------------------------------------------------------------------------- #
# Plan kind / status helpers                                                   #
# --------------------------------------------------------------------------- #
def _plan_kind_for_window(
    window_kind: WindowKind, has_deployable: bool
) -> ExecutionPlanKind:
    """Map a window kind + cash availability to an :class:`ExecutionPlanKind`.

    A deploy-only window is always ``DEPLOY``. A rebalance window is
    ``DEPLOY_AND_REBALANCE`` when there is fresh cash to fold in first (so
    selling stays last-resort), else ``REBALANCE``.
    """
    if window_kind == WINDOW_DEPLOYMENT:
        return ExecutionPlanKind.DEPLOY
    return (
        ExecutionPlanKind.DEPLOY_AND_REBALANCE
        if has_deployable
        else ExecutionPlanKind.REBALANCE
    )


def _resolve_kind(
    requested: ExecutionPlanKind | str | None,
    window_kind: WindowKind | None,
    has_deployable: bool,
) -> ExecutionPlanKind:
    """Resolve the effective plan kind.

    When ``requested`` is supplied it wins (validated against the enum).
    Otherwise the kind defaults from the open window (§19.6): outside any
    window a deploy-only plan is produced (the caller chose to generate one).
    """
    if requested is not None:
        if isinstance(requested, ExecutionPlanKind):
            return requested
        value = str(requested).upper()
        try:
            return ExecutionPlanKind(value)
        except ValueError as exc:
            raise ValidationFailed(
                f"Unknown execution plan kind {value!r}"
            ) from exc
    if window_kind is None:
        return ExecutionPlanKind.DEPLOY
    return _plan_kind_for_window(window_kind, has_deployable)


# --------------------------------------------------------------------------- #
# Allocation / order serialization                                             #
# --------------------------------------------------------------------------- #
def _current_allocation_pct(state: LedgerState, prices: dict[str, Decimal], fx_rate: Decimal) -> dict[str, str]:
    """Current allocation as ``{symbol|CASH: weight_pct_str}`` (0–100 scale).

    Decimal weights are serialized as strings so they round-trip losslessly to
    :class:`decimal.Decimal` out of the JSON column.
    """
    snapshot = valuation(state, prices, fx_rate)
    nav = snapshot.nav_usd
    allocation: dict[str, str] = {}
    for holding in snapshot.holdings:
        weight = holding.weight_pct if holding.weight_pct is not None else ZERO
        allocation[holding.symbol] = str(weight)
    cash_weight = (
        snapshot.cash_weight_pct
        if snapshot.cash_weight_pct is not None
        else (Q4(state.cash_usd * Decimal("100") / nav) if nav > ZERO else ZERO)
    )
    allocation["CASH"] = str(cash_weight)
    return allocation


def _orders_to_json(plan: RebalancePlan) -> list[dict[str, str]]:
    """Serialize rebalance orders for the plan's ``orders`` JSON column.

    Each order is ``{symbol, side, quantity, unit_price_usd, est_amount_usd,
    est_amount_myr}`` with all numerics as strings (lossless Decimal).
    """
    return [
        {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": str(order.quantity),
            "unit_price_usd": str(order.unit_price_usd),
            "est_amount_usd": str(order.est_amount_usd),
            "est_amount_myr": str(order.est_amount_myr),
        }
        for order in plan.orders
    ]


def _post_allocation_json(plan: RebalancePlan) -> dict[str, str]:
    """Serialize the plan's projected post-trade weights (0–100 scale)."""
    return {
        symbol: str(weight)
        for symbol, weight in plan.post_trade_weights_pct.items()
    }


def _violations_to_json(
    violations: list[ips_enforcement.IpsViolation],
) -> list[dict[str, Any]]:
    """Serialize IPS violations for the plan's ``ips_violations`` JSON column."""
    return [
        {
            "rule_type": violation.rule_type,
            "level": violation.level,
            "message": violation.message,
            "evidence": violation.evidence,
        }
        for violation in violations
    ]


# --------------------------------------------------------------------------- #
# Plan generation                                                              #
# --------------------------------------------------------------------------- #
async def generate_execution_plan(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal],
    fx_rate: Decimal,
    kind: ExecutionPlanKind | str | None = None,
) -> ExecutionPlan:
    """Generate and persist a DRAFT execution plan for the current window.

    ``prices`` are USD per share for every held and target symbol; ``fx_rate``
    is the USD->MYR rate used to convert deployable surplus into USD and to
    estimate MYR amounts. ``kind`` defaults from ``classify_window(today)``:

    * **DEPLOY** — deploys the deployable surplus (÷ ``fx_rate`` → USD) toward
      the IPS targets (70/30) via the Phase-1 rebalance **CASH_ONLY** path:
      buys only, floored to 4dp shares, never overspending.
    * **REBALANCE / DEPLOY_AND_REBALANCE** — runs the full Phase-1 rebalance
      (cash → contribution → sell-last) allocation correction with exact
      buy/sell share quantities; the deployable cash is folded in first so a
      sell is genuinely the last resort.

    The plan is validated through
    :func:`ips_enforcement.validate_action` (``EXECUTION_PLAN``) and persisted
    DRAFT with ``allocation_before/after``, ``orders``, ``steps``,
    ``fx_rate_used`` and ``cash_deployed_*``. Audited and committed.

    Raises :class:`ValidationFailed` on a non-positive FX rate or invalid
    window config / prices; :class:`NotFoundError` when no IPS policy exists.
    """
    if fx_rate <= ZERO:
        raise ValidationFailed("fx_rate (USD->MYR) must be positive")

    ips = await _load_ips(db, user)
    today = kl_today()
    window = current_open_window(today, ips)
    window_kind = window.kind if window is not None else None
    window_date = window.opens if window is not None else today

    surplus_myr = await deployment.cash.deployable_surplus_myr(db, user, today)
    has_deployable = surplus_myr > ZERO
    plan_kind = _resolve_kind(kind, window_kind, has_deployable)

    # Deploy-only plans never sell: only the deployable surplus is available as
    # fresh contribution. Rebalance plans also fold the surplus in first.
    deploy_usd = Q4(safe_div(surplus_myr, fx_rate)) if has_deployable else ZERO

    transactions = await _load_transactions(db, user)
    state = replay(transactions)
    allocation_before = _current_allocation_pct(state, prices, fx_rate)

    # A DEPLOY (deploy-only) plan must never sell and must never overspend the
    # deployable surplus: run the Phase-1 engine in sell-disabled (CASH_ONLY)
    # mode so every BUY is floored strictly against deployable cash, instead of
    # running a full rebalance and post-filtering its sell-funded buys (which
    # would overspend). Deploy windows are additive only (DESIGN §19.6, §19.9).
    is_deploy_only = plan_kind == ExecutionPlanKind.DEPLOY
    rebalance_plan = plan_rebalance(
        state,
        prices,
        fx_rate,
        ips,
        extra_cash_usd=deploy_usd,
        sell_disabled=is_deploy_only,
    )

    orders = list(rebalance_plan.orders)

    # Cash actually deployed by BUY orders (USD) -> MYR at the same rate.
    cash_deployed_usd = Q4(
        sum(
            (order.est_amount_usd for order in orders if order.side == "BUY"),
            start=ZERO,
        )
    )
    cash_deployed_myr = Q2(cash_deployed_usd * fx_rate)

    # Invariant guard (§19.6): a deploy-only plan can never deploy more than the
    # deployable surplus budget (the fresh contribution, ÷FX → USD).
    if is_deploy_only and cash_deployed_usd > deploy_usd + _DEPLOY_OVERSPEND_TOLERANCE:
        raise ValidationFailed(
            f"Deploy-only plan would deploy ${cash_deployed_usd} exceeding the "
            f"deployable surplus budget ${deploy_usd}"
        )

    # IPS validation of every order (asset-class + min-holding on sells).
    verdict = await ips_enforcement.validate_action(
        db,
        user,
        {
            "kind": ips_enforcement.ACTION_EXECUTION_PLAN,
            "window_date": window_date.isoformat(),
            "orders": [
                {"symbol": order.symbol, "side": order.side}
                for order in orders
            ],
        },
    )
    all_violations = verdict.violations + verdict.warnings
    ips_compliant = verdict.max_level != IpsEnforcementLevel.BLOCK.value

    # Build the persisted orders/steps from the (possibly filtered) order list.
    filtered_plan = RebalancePlan(
        status=rebalance_plan.status,
        orders=orders,
        steps=rebalance_plan.steps,
        post_trade_weights_pct=rebalance_plan.post_trade_weights_pct,
        leftover_cash_usd=rebalance_plan.leftover_cash_usd,
        max_abs_drift_pp=rebalance_plan.max_abs_drift_pp,
        priority_note=rebalance_plan.priority_note,
        message=rebalance_plan.message,
    )
    orders_json = _orders_to_json(filtered_plan)
    steps = [_step_text(idx, order) for idx, order in enumerate(orders, start=1)]
    if not steps:
        steps = ["1. Do nothing. The allocation is within policy."]

    plan = ExecutionPlan(
        user_id=user.id,
        window_date=window_date,
        plan_kind=plan_kind.value,
        status=ExecutionPlanStatus.DRAFT.value,
        cash_deployed_myr=cash_deployed_myr,
        cash_deployed_usd=cash_deployed_usd,
        fx_rate_used=Q4(fx_rate),
        allocation_before=json.dumps(allocation_before),
        allocation_after=json.dumps(_post_allocation_json(filtered_plan)),
        orders=json.dumps(orders_json),
        steps=json.dumps(steps),
        ips_compliant=ips_compliant,
        ips_violations=json.dumps(_violations_to_json(all_violations)),
    )
    db.add(plan)
    await db.flush()
    db.add(
        _audit(
            user.id,
            "EXECUTION_PLAN_GENERATE",
            plan.id,
            f"Generated {plan_kind.value} execution plan for "
            f"{window_date.isoformat()}",
            {
                "plan_kind": plan_kind.value,
                "window_date": window_date.isoformat(),
                "rebalance_status": rebalance_plan.status.value,
                "order_count": len(orders),
                "cash_deployed_myr": cash_deployed_myr,
                "ips_compliant": ips_compliant,
            },
        )
    )
    await db.commit()
    await db.refresh(plan)
    return plan


def _step_text(index: int, order: Any) -> str:
    """Human-readable step line for one order (mirrors the rebalance engine)."""
    return (
        f"{index}. {order.side.capitalize()} {order.quantity} {order.symbol} "
        f"@ ${order.unit_price_usd} ≈ ${order.est_amount_usd} "
        f"(RM{order.est_amount_myr})"
    )


# --------------------------------------------------------------------------- #
# Plan queries                                                                 #
# --------------------------------------------------------------------------- #
async def get_plan(
    db: AsyncSession, user: User, plan_id: int
) -> ExecutionPlan:
    """Fetch one of the user's execution plans; raise NotFoundError otherwise."""
    result = await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.id == plan_id,
            ExecutionPlan.user_id == user.id,
        )
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise NotFoundError(f"Execution plan {plan_id} not found")
    return plan


async def list_plans(
    db: AsyncSession,
    user: User,
    status: ExecutionPlanStatus | str | None = None,
) -> list[ExecutionPlan]:
    """List the user's execution plans (newest first), optional status filter."""
    criteria = [ExecutionPlan.user_id == user.id]
    if status is not None:
        status_value = (
            status.value
            if isinstance(status, ExecutionPlanStatus)
            else str(status)
        )
        if status_value not in {member.value for member in ExecutionPlanStatus}:
            raise ValidationFailed(f"Unknown plan status {status_value!r}")
        criteria.append(ExecutionPlan.status == status_value)
    result = await db.execute(
        select(ExecutionPlan)
        .where(*criteria)
        .order_by(ExecutionPlan.created_at.desc(), ExecutionPlan.id.desc())
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Plan lifecycle                                                               #
# --------------------------------------------------------------------------- #
def _parse_plan_orders(plan: ExecutionPlan) -> list[dict[str, Any]]:
    """Decode the plan's ``orders`` JSON into a list of order mappings."""
    try:
        decoded = json.loads(plan.orders)
    except json.JSONDecodeError as exc:  # pragma: no cover - corrupt row
        raise ValidationFailed(
            f"Execution plan {plan.id} has malformed orders JSON"
        ) from exc
    if not isinstance(decoded, list):
        raise ValidationFailed(
            f"Execution plan {plan.id} orders must be a JSON list"
        )
    return [order for order in decoded if isinstance(order, dict)]


async def approve(
    db: AsyncSession,
    user: User,
    plan_id: int,
    *,
    override: bool = False,
) -> ExecutionPlan:
    """Approve a DRAFT plan after re-validating it through IPS enforcement.

    Re-runs :func:`ips_enforcement.validate_action` over the plan's orders. A
    BLOCK-level violation without ``override`` raises
    :class:`ValidationFailed` (HTTP 422) and is audited as a critical
    ``IPS_ALERT``; an audited ``override=True`` is the sole bypass. On success
    the plan becomes ``APPROVED`` and a :class:`DeploymentIntent` is linked (if
    the plan deployed cash and an open intent exists) or created. Audited and
    committed.

    Raises :class:`ConflictError` when the plan is not in DRAFT status.
    """
    plan = await get_plan(db, user, plan_id)
    if plan.status != ExecutionPlanStatus.DRAFT.value:
        raise ConflictError(
            f"Only a DRAFT plan can be approved (plan {plan.id} is "
            f"{plan.status})"
        )

    orders = _parse_plan_orders(plan)
    verdict = await ips_enforcement.validate_action(
        db,
        user,
        {
            "kind": ips_enforcement.ACTION_EXECUTION_PLAN,
            "window_date": plan.window_date.isoformat(),
            "orders": [
                {
                    "symbol": order.get("symbol", ""),
                    "side": order.get("side"),
                }
                for order in orders
            ],
        },
        override=override,
    )
    all_violations = verdict.violations + verdict.warnings
    plan.ips_violations = json.dumps(_violations_to_json(all_violations))
    plan.ips_compliant = verdict.max_level != IpsEnforcementLevel.BLOCK.value

    if not verdict.allowed:
        # BLOCK without override: audit each blocking violation, then reject.
        for violation in verdict.violations:
            await ips_enforcement.record_block_audit(
                db, user, violation, override=False
            )
        await db.commit()
        messages = "; ".join(v.message for v in verdict.violations)
        raise ValidationFailed(
            f"Execution plan {plan.id} blocked by IPS enforcement: {messages}"
        )

    if override and verdict.violations:
        # Audited override of an otherwise-blocking plan.
        for violation in verdict.violations:
            await ips_enforcement.record_block_audit(
                db, user, violation, override=True
            )

    plan.status = ExecutionPlanStatus.APPROVED.value
    intent = await _link_or_create_intent(db, user, plan)
    db.add(
        _audit(
            user.id,
            "EXECUTION_PLAN_APPROVE",
            plan.id,
            f"Approved execution plan {plan.id}",
            {
                "override": override,
                "deployment_intent_id": intent.id if intent else None,
                "cash_deployed_myr": plan.cash_deployed_myr,
            },
        )
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def _link_or_create_intent(
    db: AsyncSession, user: User, plan: ExecutionPlan
) -> Any:
    """Link the plan to an open deployment intent, or create one (no commit).

    When the plan deploys MYR cash: if an open (QUEUED/PLANNED) intent for the
    plan's window exists it is attached and marked PLANNED; otherwise a
    WINDOW-triggered intent is created and attached. A pure-rebalance plan with
    no fresh cash creates no intent. The intent rows are flushed but committed
    by :func:`approve` as one unit.
    """
    deployed = Q4(plan.cash_deployed_myr)
    if deployed <= ZERO:
        return None

    open_statuses = (
        deployment.DeploymentStatus.QUEUED.value,
        deployment.DeploymentStatus.PLANNED.value,
    )
    existing = await db.execute(
        select(deployment.DeploymentIntent).where(
            deployment.DeploymentIntent.user_id == user.id,
            deployment.DeploymentIntent.status.in_(open_statuses),
            deployment.DeploymentIntent.target_window_date == plan.window_date,
        )
    )
    intent = existing.scalars().first()
    if intent is None:
        intent = deployment.DeploymentIntent(
            user_id=user.id,
            source_account_id=None,
            amount_myr=deployed,
            trigger=DeploymentTrigger.WINDOW.value,
            status=deployment.DeploymentStatus.PLANNED.value,
            target_window_date=plan.window_date,
            execution_plan_id=plan.id,
            notes=(
                f"Created from execution plan {plan.id} approval for the "
                f"{plan.window_date.isoformat()} window"
            ),
        )
        db.add(intent)
        await db.flush()
        return intent

    intent.execution_plan_id = plan.id
    intent.status = deployment.DeploymentStatus.PLANNED.value
    await db.flush()
    return intent


async def execute(
    db: AsyncSession, user: User, plan_id: int
) -> ExecutionPlan:
    """Mark an APPROVED plan ``EXECUTED`` (sets ``executed_at``). Audited.

    The plan's orders are the recommended trades; recording them as broker
    transactions and emitting the cash movements is the caller's separate,
    explicit step (the portfolio stays derived from the ledger). Raises
    :class:`ConflictError` when the plan is not APPROVED.
    """
    plan = await get_plan(db, user, plan_id)
    if plan.status != ExecutionPlanStatus.APPROVED.value:
        raise ConflictError(
            f"Only an APPROVED plan can be executed (plan {plan.id} is "
            f"{plan.status})"
        )
    plan.status = ExecutionPlanStatus.EXECUTED.value
    plan.executed_at = _utc_now()
    db.add(
        _audit(
            user.id,
            "EXECUTION_PLAN_EXECUTE",
            plan.id,
            f"Executed execution plan {plan.id}",
            {"cash_deployed_myr": plan.cash_deployed_myr},
        )
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def skip(
    db: AsyncSession, user: User, plan_id: int
) -> ExecutionPlan:
    """Mark a DRAFT/APPROVED plan ``SKIPPED`` (discipline-preserving). Audited.

    Raises :class:`ConflictError` when the plan is already EXECUTED, SKIPPED or
    EXPIRED.
    """
    plan = await get_plan(db, user, plan_id)
    if plan.status not in (
        ExecutionPlanStatus.DRAFT.value,
        ExecutionPlanStatus.APPROVED.value,
    ):
        raise ConflictError(
            f"Only a DRAFT or APPROVED plan can be skipped (plan {plan.id} is "
            f"{plan.status})"
        )
    plan.status = ExecutionPlanStatus.SKIPPED.value
    db.add(
        _audit(
            user.id,
            "EXECUTION_PLAN_SKIP",
            plan.id,
            f"Skipped execution plan {plan.id}",
            {"window_date": plan.window_date.isoformat()},
        )
    )
    await db.commit()
    await db.refresh(plan)
    return plan


def _utc_now() -> datetime:
    """Current timezone-aware UTC datetime for ``executed_at`` stamps."""
    return datetime.now(timezone.utc)

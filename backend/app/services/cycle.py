"""Wealth Operating Cycle Engine (DESIGN §19.2, Decision Log 21).

The current life-cycle state is a **pure function** of (date vs windows,
deployable cash, drift, active intents/plans) and is *never* stored
authoritatively. :func:`current_state` derives the state; :func:`log_transition`
appends a :class:`~app.models.cycle.CycleStateLog` row only when the state
changes from the latest logged entry (dedupe). Read-only callers leave
``log=False`` so they never write.

The cycle never schedules windows itself: every window question is answered by
the single Unified Execution Window Engine (``services.execution``, §19.6) —
:func:`~app.services.execution.classify_window`,
:func:`~app.services.execution.current_open_window` and
:func:`~app.services.execution.next_window`.

Deterministic precedence (first match wins, §19.2):

1. **REBALANCE_WINDOW** — ``classify_window(today) == REBALANCE`` *or* a
   scheduled rebalance is overdue (a rebalance window has fully passed since
   the last EXECUTED rebalance plan / anchor while drift was beyond threshold).
2. **DEPLOYMENT** — an active ``deployment_intent`` (QUEUED/PLANNED) or an
   unexecuted ``execution_plan`` (DRAFT/APPROVED) exists, *or*
   ``classify_window(today) == DEPLOYMENT`` with ``deployable_surplus ≥
   min_deploy_threshold``.
3. **READY_TO_DEPLOY** — ``deployable_surplus_myr ≥ min_deploy_threshold_myr``.
4. **ACCUMULATION** — the disciplined default.

Drift requires ``prices`` (USD per held symbol) and ``fx_rate`` (USD->MYR).
When either is absent drift is **unknown**: the non-drift signals still drive
the state, and the overdue-rebalance check (which needs drift) treats unknown
drift as **not overdue** (documented fallback, §19.2). Units: ``*_myr`` are
MYR, ``*_pp`` are percentage points; floats never touch any value here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.cycle import CycleStateLog, WealthCycleState
from app.models.deployment import DeploymentIntent, DeploymentStatus
from app.models.execution import (
    ExecutionPlan,
    ExecutionPlanKind,
    ExecutionPlanStatus,
)
from app.models.ips import IpsRule
from app.models.transaction import Transaction
from app.models.user import User
from app.services import cash, execution
from app.services.drift import drift
from app.services.valuation import valuation
from app.services.ledger import replay
from app.utils.dates import kl_today
from app.utils.money import Q4

# Intent statuses that count as an "active" (still-actionable) deployment.
_ACTIVE_INTENT_STATUSES: Final[tuple[str, ...]] = (
    DeploymentStatus.QUEUED.value,
    DeploymentStatus.PLANNED.value,
)
# Plan statuses that count as an "unexecuted" (still-pending) execution plan.
_UNEXECUTED_PLAN_STATUSES: Final[tuple[str, ...]] = (
    ExecutionPlanStatus.DRAFT.value,
    ExecutionPlanStatus.APPROVED.value,
)
# Rebalance-bearing plan kinds (a plan that actually corrected allocation).
_REBALANCE_PLAN_KINDS: Final[tuple[str, ...]] = (
    ExecutionPlanKind.REBALANCE.value,
    ExecutionPlanKind.DEPLOY_AND_REBALANCE.value,
)
# One calendar day, used to step past a window opening in the overdue scan.
_ONE_DAY: Final[timedelta] = timedelta(days=1)
# Upper bound on rebalance-window steps in the overdue scan (defensive; the
# loop returns on the first fully-passed or still-open rebalance window).
_OVERDUE_SCAN_LIMIT: Final[int] = 240


@dataclass(frozen=True)
class CycleState:
    """The derived wealth operating-cycle state and its supporting signals.

    ``state`` is a :class:`~app.models.cycle.WealthCycleState` value;
    ``since`` is the UTC datetime the state was entered (from the matching
    latest log row when one exists, else ``computed_at``); ``context`` is the
    JSON-serializable signal snapshot consumed by the Action Status engine and
    the API.

    ``context`` keys (§19.2 + the engine's window detail):

    * ``deployable_myr`` — MYR deployable surplus (str Decimal).
    * ``drift_max_pp`` — max absolute drift in pp (str Decimal) or ``None``
      when drift is unknown (no ``prices``/``fx_rate``).
    * ``open_window`` — ``True``/``False`` (is an execution window open today).
    * ``window_kind`` — ``"DEPLOYMENT"``/``"REBALANCE"``/``None`` (open kind).
    * ``next_window_date`` — ISO date of the next window opening.
    * ``next_window_kind`` — kind of that next window.
    * ``active_intents`` — count of QUEUED/PLANNED deployment intents.
    * ``unexecuted_plans`` — count of DRAFT/APPROVED execution plans.
    * ``last_rebalance_date`` — ISO date of the last EXECUTED rebalance plan,
      or ``None``.
    * ``rebalance_overdue`` — ``True`` when a rebalance window fully passed
      since the last rebalance while drift was beyond threshold (``False`` when
      drift is unknown — documented fallback).
    """

    state: WealthCycleState
    since: datetime
    context: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Loading helpers (per-user isolated)                                          #
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


async def _count_active_intents(db: AsyncSession, user: User) -> int:
    """Count the user's active (QUEUED/PLANNED) deployment intents."""
    result = await db.execute(
        select(DeploymentIntent.id).where(
            DeploymentIntent.user_id == user.id,
            DeploymentIntent.status.in_(_ACTIVE_INTENT_STATUSES),
        )
    )
    return len(result.scalars().all())


async def _count_unexecuted_plans(db: AsyncSession, user: User) -> int:
    """Count the user's unexecuted (DRAFT/APPROVED) execution plans."""
    result = await db.execute(
        select(ExecutionPlan.id).where(
            ExecutionPlan.user_id == user.id,
            ExecutionPlan.status.in_(_UNEXECUTED_PLAN_STATUSES),
        )
    )
    return len(result.scalars().all())


async def _last_executed_rebalance_date(
    db: AsyncSession, user: User
) -> date | None:
    """Window date of the most recent EXECUTED rebalance plan, or ``None``.

    A rebalance plan is a ``REBALANCE`` or ``DEPLOY_AND_REBALANCE`` plan (both
    correct allocation); a deploy-only plan does not reset the rebalance
    anchor. Used as the anchor for the overdue-rebalance check (§19.2).
    """
    result = await db.execute(
        select(ExecutionPlan.window_date)
        .where(
            ExecutionPlan.user_id == user.id,
            ExecutionPlan.status == ExecutionPlanStatus.EXECUTED.value,
            ExecutionPlan.plan_kind.in_(_REBALANCE_PLAN_KINDS),
        )
        .order_by(ExecutionPlan.window_date.desc())
    )
    return result.scalars().first()


# --------------------------------------------------------------------------- #
# Drift signal (unknown when unpriced)                                         #
# --------------------------------------------------------------------------- #
def _drift_signal(
    transactions: list[Transaction],
    ips: IpsRule,
    prices: dict[str, Decimal] | None,
    fx_rate: Decimal | None,
) -> tuple[Decimal | None, bool]:
    """Return ``(max_abs_drift_pp, beyond_threshold)`` or ``(None, False)``.

    Drift needs both ``prices`` and ``fx_rate``; when either is missing drift
    is **unknown** and the caller must treat it accordingly (the
    overdue-rebalance check then treats it as not overdue, §19.2). When known,
    ``beyond_threshold`` is ``max|drift| > drift_threshold_pct``.
    """
    if prices is None or fx_rate is None:
        return None, False
    state = replay(transactions)
    report = drift(valuation(state, prices, fx_rate), ips)
    return report.max_abs_drift_pp, not report.within_threshold


# --------------------------------------------------------------------------- #
# Overdue-rebalance check (requires drift)                                     #
# --------------------------------------------------------------------------- #
def _rebalance_overdue(
    today: date,
    ips: IpsRule,
    last_rebalance_date: date | None,
    transactions: list[Transaction],
    drift_beyond_threshold: bool,
    drift_known: bool,
) -> bool:
    """Whether a scheduled rebalance is overdue at ``today`` (§19.2 rule 1).

    Overdue means a rebalance window has *fully passed* (its inclusive close is
    strictly before ``today``) since the anchor — the later of the last
    EXECUTED rebalance plan's window date and the first transaction date —
    **while drift is currently beyond threshold**.

    Drift fallback (documented, §19.2): when drift is unknown (no
    ``prices``/``fx_rate``) this returns ``False`` — unknown drift is treated
    as *not overdue* so a read without market data never raises a false
    REBALANCE_WINDOW alarm. Today's own open rebalance window is handled by
    rule 1's ``classify_window`` clause, not here.
    """
    if not drift_known or not drift_beyond_threshold:
        return False

    # Anchor: the later of the last executed rebalance and the first txn date.
    anchor = last_rebalance_date
    if transactions:
        first_txn = min(txn.transaction_date for txn in transactions)
        anchor = max(anchor, first_txn) if anchor is not None else first_txn
    if anchor is None:
        # No ledger history and no prior rebalance — nothing can be overdue.
        return False

    # Walk forward over execution windows, looking for the first REBALANCE
    # window that opens strictly after the anchor. `next_window` reports the
    # window *currently open at* its argument, so the cursor is always advanced
    # past the returned window's inclusive close date to guarantee progress
    # (otherwise a cursor inside an open window would loop on it forever).
    cursor = anchor + _ONE_DAY
    # Bounded scan: rebalance windows recur on a small month cadence; the loop
    # returns on the first rebalance window found (passed → overdue, else not).
    for _ in range(_OVERDUE_SCAN_LIMIT):
        next_date, next_kind = execution.next_window(cursor, ips)
        window = execution.current_open_window(next_date, ips)
        close_date = window.closes if window is not None else next_date
        if next_kind == execution.WINDOW_REBALANCE and next_date > anchor:
            # The first rebalance window after the anchor: overdue iff it has
            # fully passed (closed strictly before today) with drift still
            # beyond threshold; otherwise it is current/future → not overdue.
            return close_date < today
        # Not a (post-anchor) rebalance window — skip past its close and look
        # for the next window.
        cursor = close_date + _ONE_DAY
    return False


# --------------------------------------------------------------------------- #
# State derivation                                                             #
# --------------------------------------------------------------------------- #
def _utc_now() -> datetime:
    """Current timezone-aware UTC datetime for ``since`` stamps."""
    return datetime.now(timezone.utc)


async def current_state(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
    today: date | None = None,
    *,
    log: bool = False,
) -> CycleState:
    """Derive the user's current wealth operating-cycle state (§19.2).

    Evaluates the §19.2 precedence (first match wins) using
    :func:`~app.services.execution.classify_window` as the ONLY window source.
    ``prices`` (USD per held symbol) + ``fx_rate`` (USD->MYR) enable the drift
    signal; without them drift is reported as unknown and the
    overdue-rebalance check treats it as not overdue.

    When ``log`` is ``True`` a :class:`~app.models.cycle.CycleStateLog` row is
    appended *only* if the derived state differs from the latest logged entry
    (dedupe, via :func:`log_transition`); read-only callers leave ``log``
    ``False`` so they never write. Returns the :class:`CycleState` whose
    ``context`` is the signal snapshot consumed by the Action Status engine and
    the API.

    Raises :class:`~app.core.errors.NotFoundError` when the user has no IPS
    policy row.
    """
    when = today if today is not None else kl_today()
    ips = await _load_ips(db, user)
    transactions = await _load_transactions(db, user)

    # --- Window signals (single source: execution.classify_window) ---
    window_kind = execution.classify_window(when, ips)
    open_window = window_kind is not None
    # next_window_date is the next FUTURE opening (never the current open
    # window's past anchor) per the §19.3/§19.7 contract.
    next_date, next_kind = execution.next_future_window(when, ips)

    # --- Cash signals ---
    deployable_myr = await cash.deployable_surplus_myr(db, user, when)
    threshold_myr = Q4(ips.min_deploy_threshold_myr)
    cash_ready = deployable_myr >= threshold_myr

    # --- Drift signal (unknown when unpriced) ---
    drift_max_pp, drift_beyond = _drift_signal(
        transactions, ips, prices, fx_rate
    )
    drift_known = drift_max_pp is not None

    # --- Queue / plan signals ---
    active_intents = await _count_active_intents(db, user)
    unexecuted_plans = await _count_unexecuted_plans(db, user)
    last_rebalance_date = await _last_executed_rebalance_date(db, user)

    overdue = _rebalance_overdue(
        when,
        ips,
        last_rebalance_date,
        transactions,
        drift_beyond,
        drift_known,
    )

    # --- Deterministic precedence (first match wins, §19.2) ---
    if window_kind == execution.WINDOW_REBALANCE or overdue:
        state = WealthCycleState.REBALANCE_WINDOW
    elif (
        active_intents > 0
        or unexecuted_plans > 0
        or (window_kind == execution.WINDOW_DEPLOYMENT and cash_ready)
    ):
        state = WealthCycleState.DEPLOYMENT
    elif cash_ready:
        state = WealthCycleState.READY_TO_DEPLOY
    else:
        state = WealthCycleState.ACCUMULATION

    context: dict[str, Any] = {
        "deployable_myr": str(deployable_myr),
        "drift_max_pp": str(drift_max_pp) if drift_known else None,
        "open_window": open_window,
        "window_kind": window_kind,
        "next_window_date": next_date.isoformat(),
        "next_window_kind": next_kind,
        "active_intents": active_intents,
        "unexecuted_plans": unexecuted_plans,
        "last_rebalance_date": (
            last_rebalance_date.isoformat()
            if last_rebalance_date is not None
            else None
        ),
        "rebalance_overdue": overdue,
    }

    since = await _state_since(db, user, state)
    if log:
        logged = await log_transition(db, user, state, context)
        if logged is not None:
            since = logged.entered_at

    return CycleState(state=state, since=since, context=context)


async def _latest_log(
    db: AsyncSession, user: User
) -> CycleStateLog | None:
    """Return the user's most recent cycle-state log row, or ``None``."""
    result = await db.execute(
        select(CycleStateLog)
        .where(CycleStateLog.user_id == user.id)
        .order_by(
            CycleStateLog.entered_at.desc(), CycleStateLog.id.desc()
        )
    )
    return result.scalars().first()


async def _state_since(
    db: AsyncSession, user: User, state: WealthCycleState
) -> datetime:
    """When the current ``state`` was entered.

    Returns the ``entered_at`` of the latest log row when it already records
    this state (the state has been continuous since then); otherwise the
    current UTC instant (a fresh, not-yet-logged transition).
    """
    latest = await _latest_log(db, user)
    if latest is not None and latest.state == state.value:
        return latest.entered_at
    return _utc_now()


async def log_transition(
    db: AsyncSession,
    user: User,
    new_state: WealthCycleState,
    context: dict[str, Any],
) -> CycleStateLog | None:
    """Append a :class:`CycleStateLog` row when the state actually changed.

    Dedupe (§19.2, Decision Log 21): compares ``new_state`` against the latest
    log row for the user and inserts a new row only when they differ (or when
    there is no prior row). Returns the inserted row, or ``None`` when the
    state is unchanged (no row written). Flushes and commits atomically so the
    history capture is durable.

    The log is history/reporting only — it is never read back as the
    authoritative state (which is always derived by :func:`current_state`).
    """
    latest = await _latest_log(db, user)
    if latest is not None and latest.state == new_state.value:
        return None

    row = CycleStateLog(
        user_id=user.id,
        state=new_state.value,
        entered_at=_utc_now(),
        context=json.dumps(context, default=str),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

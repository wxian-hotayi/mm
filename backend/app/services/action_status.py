"""Action Status Engine (DESIGN §19.3, §19.9) — the single system-wide signal.

This is the **single most important output** of WealthOS (Decision Log 24): a
pure, deterministic decision that collapses every upstream engine into one of
three answers::

    DO_NOTHING | REVIEW_REQUIRED | REBALANCE_NOW

with display labels *Do Nothing* / *Review* / *Rebalance Now*
(``REVIEW`` ≡ ``REVIEW_REQUIRED``). There is **no LLM**: the status is composed
deterministically from the existing engines, never invented.

It sits at the bottom of the canonical pipeline (§19.9), consuming:

* the Wealth Operating Cycle state (:func:`app.services.cycle.current_state`);
* IPS enforcement — compliance score, standing alerts
  (:mod:`app.services.ips_enforcement`);
* behavior flags (Phase-1 :func:`app.services.behavior.compute_flags`);
* allocation drift (:mod:`app.services.drift` via the cycle context / a fresh
  valuation for cash drag);
* deployable surplus (:mod:`app.services.cash`);
* the unified execution window schedule
  (:mod:`app.services.execution`); and
* pending DRAFT :class:`~app.models.execution.ExecutionPlan` rows.

Decision rules (EXACTLY §19.3, first match wins):

* **REBALANCE_NOW** — ``cycle_state == REBALANCE_WINDOW`` *and*
  (``max|drift| > threshold`` *or* a scheduled rebalance is overdue). The only
  status that asks the user to actively trade/sell.
* **REVIEW_REQUIRED** — any of: ``READY_TO_DEPLOY``/``DEPLOYMENT`` with
  deployable cash; an active IPS violation (any enforcement level); a behavior
  flag ≥ WARNING; ``max|drift| > 0.7 × threshold`` (approaching); the next
  window opens within ``review_lead_days``; a DRAFT execution plan awaiting
  approval.
* **DO_NOTHING** — none of the above: accumulating, within policy, no window,
  no flags. *The disciplined default — the system is explicitly designed to
  return this and to treat it as success (philosophy §1).*

Everything is async, ledger-first, pure-Decimal and per-user isolated. Drift /
cash-drag reasons require both ``prices`` (USD per held symbol) and ``fx_rate``
(USD->MYR); when either is missing those reasons are skipped cleanly
(documented), exactly as the underlying cycle and IPS engines do. This function
**only reads** — it never writes the cycle log or the audit trail.

Units: ``*_myr`` are MYR, ``*_pp`` are percentage points, the compliance score
is a plain ``int`` on the 0–100 scale. Floats never touch any value here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditSeverity
from app.models.cycle import WealthCycleState
from app.models.execution import ExecutionPlan, ExecutionPlanStatus
from app.models.user import User
from app.services import behavior, cycle, execution, ips_enforcement
from app.services.cycle import CycleState
from app.services.drift import drift
from app.services.ledger import replay
from app.services.valuation import valuation
from app.utils.dates import kl_today
from app.utils.money import Q4, ZERO

# --- Status values (DESIGN §19.3 / §19.9) ---------------------------------
STATUS_DO_NOTHING: Final[str] = "DO_NOTHING"
STATUS_REVIEW_REQUIRED: Final[str] = "REVIEW_REQUIRED"
STATUS_REBALANCE_NOW: Final[str] = "REBALANCE_NOW"

#: Display labels keyed by status value (``REVIEW`` ≡ ``REVIEW_REQUIRED``).
STATUS_LABELS: Final[dict[str, str]] = {
    STATUS_DO_NOTHING: "Do Nothing",
    STATUS_REVIEW_REQUIRED: "Review",
    STATUS_REBALANCE_NOW: "Rebalance Now",
}

# --- Reason severities (mirror AuditSeverity) -----------------------------
SEVERITY_INFO: Final[str] = AuditSeverity.INFO.value
SEVERITY_WARNING: Final[str] = AuditSeverity.WARNING.value
SEVERITY_CRITICAL: Final[str] = AuditSeverity.CRITICAL.value

# --- Reason codes (stable identifiers; consumed by the API + UI later) ----
REASON_REBALANCE_DRIFT: Final[str] = "REBALANCE_DRIFT"
REASON_REBALANCE_OVERDUE: Final[str] = "REBALANCE_OVERDUE"
REASON_DEPLOYABLE_CASH: Final[str] = "DEPLOYABLE_CASH"
REASON_IPS_VIOLATION: Final[str] = "IPS_VIOLATION"
REASON_BEHAVIOR_FLAG: Final[str] = "BEHAVIOR_FLAG"
REASON_DRIFT_APPROACHING: Final[str] = "DRIFT_APPROACHING"
REASON_WINDOW_SOON: Final[str] = "WINDOW_SOON"
REASON_WINDOW_OPEN: Final[str] = "WINDOW_OPEN"
REASON_ACTIVE_INTENT: Final[str] = "ACTIVE_INTENT"
REASON_DRAFT_PLAN: Final[str] = "DRAFT_PLAN"
REASON_ON_TRACK: Final[str] = "ON_TRACK"

# Behavior-flag severities that count as "≥ WARNING" for the review trigger.
_BEHAVIOR_REVIEW_SEVERITIES: Final[frozenset[str]] = frozenset(
    {SEVERITY_WARNING, SEVERITY_CRITICAL}
)
_BEHAVIOR_SEVERITY_RANK: Final[dict[str, int]] = {
    SEVERITY_INFO: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_CRITICAL: 2,
}
# A drift within this fraction of the threshold is "approaching" (§19.3).
_APPROACHING_FRACTION: Final[Decimal] = Decimal("0.7")
# Cycle states that carry deployable cash worth a review (§19.3). A REBALANCE
# window is also a deployment window (REBALANCE ⊂ DEPLOYMENT, §19.6), so cash is
# deployable there too — the deployable-cash review reason additionally fires
# whenever an execution window is open (see ``compute``), not only these states.
_DEPLOYABLE_CASH_STATES: Final[frozenset[WealthCycleState]] = frozenset(
    {
        WealthCycleState.READY_TO_DEPLOY,
        WealthCycleState.DEPLOYMENT,
        WealthCycleState.REBALANCE_WINDOW,
    }
)
# Bound on the scan for the next REBALANCE window (rebalance windows recur on a
# small month cadence; the loop returns on the first one found).
_NEXT_REBALANCE_SCAN_LIMIT: Final[int] = 240


@dataclass(frozen=True)
class Reason:
    """One driver behind the Action Status decision.

    ``code`` is a stable ``REASON_*`` identifier; ``severity`` is an
    :class:`~app.models.audit.AuditSeverity` value (INFO/WARNING/CRITICAL);
    ``message`` is human-readable. JSON-serializes as ``{code, message,
    severity}`` (DESIGN §19.3).
    """

    code: str
    message: str
    severity: str


@dataclass(frozen=True)
class ActionStatus:
    """The single system-wide Action Status decision (DESIGN §19.3, §19.9).

    Attribute summary (matching the §19.3 response shape exactly):

    * ``status`` — ``DO_NOTHING`` / ``REVIEW_REQUIRED`` / ``REBALANCE_NOW``.
    * ``label`` — the human display label for ``status`` (``STATUS_LABELS``).
    * ``headline`` — one-line summary (reassuring on ``DO_NOTHING``).
    * ``reasons`` — every condition that fired, most-severe first.
    * ``primary_action`` — the single next step the user should take.
    * ``next_window_date`` — ISO date of the next execution window opening.
    * ``next_rebalance_date`` — ISO date of the next REBALANCE window opening.
    * ``compliance_score`` — IPS compliance score (0–100).
    * ``cycle_state`` — the underlying :class:`WealthCycleState` value.
    * ``signals`` — ``{deployable_myr, max_drift_pp, cash_drag_pp,
      behavior_flag_count, ips_violation_count}`` (Decimals as strings,
      ``max_drift_pp``/``cash_drag_pp`` ``None`` when unpriced).
    * ``computed_at`` — ISO 8601 UTC timestamp of this computation.
    """

    status: str
    label: str
    headline: str
    reasons: list[Reason]
    primary_action: str
    next_window_date: str
    next_rebalance_date: str | None
    compliance_score: int
    cycle_state: str
    signals: dict[str, Any] = field(default_factory=dict)
    computed_at: str = ""


# --------------------------------------------------------------------------- #
# Pure signal helpers                                                          #
# --------------------------------------------------------------------------- #
def _utc_now_iso() -> str:
    """Current instant as an ISO 8601 UTC timestamp for ``computed_at``."""
    return datetime.now(timezone.utc).isoformat()


def _parse_drift_max_pp(context: dict[str, Any]) -> Decimal | None:
    """Extract ``max|drift|`` (pp) from a cycle context, ``None`` if unknown.

    The cycle stores ``drift_max_pp`` as a Decimal string (or ``None`` when no
    ``prices``/``fx_rate`` were supplied); this round-trips it losslessly.
    """
    raw = context.get("drift_max_pp")
    if raw is None:
        return None
    return Q4(Decimal(str(raw)))


def _parse_deployable(context: dict[str, Any]) -> Decimal:
    """Extract the MYR deployable surplus (4dp) from a cycle context."""
    raw = context.get("deployable_myr")
    return Q4(Decimal(str(raw))) if raw is not None else ZERO


async def _cash_drag_pp(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None,
    fx_rate: Decimal | None,
) -> Decimal | None:
    """Excess cash drag in pp (cash weight − ``max_cash_drag_pct``), or ``None``.

    Requires both ``prices`` and ``fx_rate``; without them cash drag is unknown
    and ``None`` is returned (documented §19.3 fallback). Reads the user's
    ledger + IPS only — never mutates. Positive means idle cash above policy.
    """
    if prices is None or fx_rate is None:
        return None
    ips = await execution._load_ips(db, user)  # noqa: SLF001 - shared loader
    transactions = await execution._load_transactions(  # noqa: SLF001
        db, user
    )
    report = drift(valuation(replay(transactions), prices, fx_rate), ips)
    return report.cash_drag_pp


async def _count_draft_plans(db: AsyncSession, user: User) -> int:
    """Count the user's DRAFT execution plans awaiting approval (§19.3)."""
    result = await db.execute(
        select(ExecutionPlan.id).where(
            ExecutionPlan.user_id == user.id,
            ExecutionPlan.status == ExecutionPlanStatus.DRAFT.value,
        )
    )
    return len(result.scalars().all())


def _next_rebalance_date(today: date, ips: Any) -> date | None:
    """ISO-free next REBALANCE window opening on or after ``today``.

    Walks the unified window schedule via
    :func:`app.services.execution.next_window` (the single scheduler), skipping
    deploy-only windows until the first REBALANCE window is found. Returns
    ``None`` only if no rebalance window exists within the scan horizon
    (a pathological config).
    """
    next_date, next_kind = execution.next_window(today, ips)
    cursor = next_date
    for _ in range(_NEXT_REBALANCE_SCAN_LIMIT):
        scan_date, scan_kind = execution.next_window(cursor, ips)
        if scan_kind == execution.WINDOW_REBALANCE:
            return scan_date
        # Advance past this deploy-only window's open month to the next one.
        window = execution.current_open_window(scan_date, ips)
        step_from = window.closes if window is not None else scan_date
        cursor = step_from + timedelta(days=1)
    return None


def _days_until(target: date, today: date) -> int:
    """Whole calendar days from ``today`` to ``target`` (negative if past)."""
    return (target - today).days


# --------------------------------------------------------------------------- #
# Headline / primary-action copy                                               #
# --------------------------------------------------------------------------- #
def _headline_and_action(
    status: str,
    reasons: list[Reason],
    deployable_myr: Decimal,
    cycle_state: str,
) -> tuple[str, str]:
    """Compose the headline + primary action for a resolved ``status``.

    Deterministic, numbers-first copy aligned with the philosophy (§1): a
    ``DO_NOTHING`` headline reassures and treats inaction as success.
    """
    if status == STATUS_REBALANCE_NOW:
        return (
            "Rebalance now — allocation has drifted beyond policy in an open "
            "rebalance window.",
            "Generate and review the rebalance execution plan, then place the "
            "exact buy/sell orders it lists.",
        )
    if status == STATUS_REVIEW_REQUIRED:
        lead = reasons[0].message if reasons else "An item needs your review."
        if cycle_state in (
            WealthCycleState.READY_TO_DEPLOY.value,
            WealthCycleState.DEPLOYMENT.value,
            WealthCycleState.REBALANCE_WINDOW.value,
        ) and deployable_myr > ZERO:
            action = (
                f"Review the deployment plan for RM{deployable_myr} of "
                "deployable cash and deploy toward your 70/30 targets."
            )
        else:
            action = (
                "Review the flagged item(s) below; no forced trading — act "
                "only if the review confirms it."
            )
        return (f"Review required — {lead}", action)
    # DO_NOTHING — the disciplined default; inaction is success.
    return (
        "Nothing to do. You are within policy and on plan — discipline beats "
        "intelligence.",
        "Continue your scheduled monthly contribution. No trading required.",
    )


# --------------------------------------------------------------------------- #
# The decision                                                                 #
# --------------------------------------------------------------------------- #
def _severity_rank(reason: Reason) -> int:
    """Rank a reason by severity (CRITICAL > WARNING > INFO) for ordering."""
    return _BEHAVIOR_SEVERITY_RANK.get(reason.severity, 0)


async def compute(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
    today: date | None = None,
) -> ActionStatus:
    """Compute the user's Action Status (DESIGN §19.3, §19.9) — pure read.

    Composes the cycle state, IPS compliance/alerts, behavior flags, drift,
    deployable surplus, window schedule and pending DRAFT plans into a single
    ``DO_NOTHING`` / ``REVIEW_REQUIRED`` / ``REBALANCE_NOW`` decision via the
    exact §19.3 rules (first match wins). ``prices`` (USD per held symbol) and
    ``fx_rate`` (USD->MYR) enable the drift / cash-drag reasons; without them
    those reasons are skipped cleanly (the cycle/IPS engines already do this).

    This function never writes — it derives the signal read-only (the cycle log
    and audit trail are written by their own engines on mutation paths). Returns
    a fully-populated :class:`ActionStatus`.

    Raises :class:`~app.core.errors.NotFoundError` when the user has no IPS
    policy row (propagated from the underlying engines).
    """
    when = today if today is not None else kl_today()

    # --- Underlying engines (all read-only here) ---
    cycle_state: CycleState = await cycle.current_state(
        db, user, prices=prices, fx_rate=fx_rate, today=when, log=False
    )
    context = cycle_state.context
    state = cycle_state.state

    ips = await execution._load_ips(db, user)  # noqa: SLF001 - shared loader
    compliance_score = await ips_enforcement.compute_compliance_score(
        db, user, prices=prices, fx_rate=fx_rate
    )
    ips_alerts = await ips_enforcement.alerts(
        db, user, prices=prices, fx_rate=fx_rate
    )
    transactions = await execution._load_transactions(  # noqa: SLF001
        db, user
    )
    behavior_flags = behavior.compute_flags(
        transactions, ips, prices=prices, fx_rate=fx_rate, today=when
    )
    draft_plans = await _count_draft_plans(db, user)

    # --- Signals ---
    deployable_myr = _parse_deployable(context)
    drift_max_pp = _parse_drift_max_pp(context)
    drift_known = drift_max_pp is not None
    drift_beyond = drift_known and drift_max_pp > ips.drift_threshold_pct
    rebalance_overdue = bool(context.get("rebalance_overdue", False))
    open_window = bool(context.get("open_window", False))
    open_window_kind = context.get("window_kind")
    active_intents = int(context.get("active_intents", 0) or 0)
    deploy_threshold = Q4(ips.min_deploy_threshold_myr)
    cash_drag = await _cash_drag_pp(db, user, prices, fx_rate)
    severe_behavior_flags = [
        flag
        for flag in behavior_flags
        if flag.severity in _BEHAVIOR_REVIEW_SEVERITIES
    ]
    threshold = Q4(ips.drift_threshold_pct)
    approaching_drift = drift_known and (
        drift_max_pp > _APPROACHING_FRACTION * threshold
    )

    # Next window + next rebalance window (the single scheduler). The cycle
    # context already supplies the next FUTURE opening; fall back to it directly
    # so the WINDOW_SOON lead time is measured against a future date, never a
    # past anchor of a currently-open window (§19.3/§19.7).
    next_window_date = context.get("next_window_date")
    if next_window_date is None:
        next_window_date, _ = execution.next_future_window(when, ips)
        next_window_date = next_window_date.isoformat()
    next_window_obj = date.fromisoformat(next_window_date)
    days_to_window = _days_until(next_window_obj, when)
    next_rebalance = _next_rebalance_date(when, ips)

    reasons: list[Reason] = []

    # ------------------------------------------------------------------ #
    # 1. REBALANCE_NOW (§19.3): in a rebalance window AND (drift>thr OR    #
    #    scheduled rebalance overdue).                                     #
    # ------------------------------------------------------------------ #
    in_rebalance_window = state == WealthCycleState.REBALANCE_WINDOW
    if in_rebalance_window and (drift_beyond or rebalance_overdue):
        if drift_beyond:
            reasons.append(
                Reason(
                    code=REASON_REBALANCE_DRIFT,
                    message=(
                        f"Allocation drift {drift_max_pp}pp exceeds the "
                        f"{threshold}pp policy threshold inside an open "
                        "rebalance window."
                    ),
                    severity=SEVERITY_CRITICAL,
                )
            )
        if rebalance_overdue:
            reasons.append(
                Reason(
                    code=REASON_REBALANCE_OVERDUE,
                    message=(
                        "A scheduled rebalance window has passed with drift "
                        "beyond threshold — the rebalance is overdue."
                    ),
                    severity=SEVERITY_CRITICAL,
                )
            )
        status = STATUS_REBALANCE_NOW

    else:
        # -------------------------------------------------------------- #
        # 2. REVIEW_REQUIRED (§19.3): any of the listed conditions.       #
        # -------------------------------------------------------------- #
        # Deployable cash worth a review: in a deployment-capable cycle state
        # (READY_TO_DEPLOY / DEPLOYMENT / REBALANCE_WINDOW — a rebalance window
        # is also a deployment window, §19.6) OR whenever any execution window
        # is open and the surplus is at/above the deploy threshold. This makes
        # the trigger reachable inside a rebalance window with no drift, instead
        # of falling through to DO_NOTHING.
        deployable_in_state = (
            state in _DEPLOYABLE_CASH_STATES and deployable_myr > ZERO
        )
        deployable_in_open_window = (
            open_window and deployable_myr >= deploy_threshold
        )
        if deployable_in_state or deployable_in_open_window:
            reasons.append(
                Reason(
                    code=REASON_DEPLOYABLE_CASH,
                    message=(
                        f"RM{deployable_myr} of deployable cash is ready to "
                        f"put to work (cycle state {state.value})."
                    ),
                    severity=SEVERITY_WARNING,
                )
            )
        # An active (QUEUED/PLANNED) deployment intent is a pending operational
        # action — surface it as a review even inside a rebalance window where
        # the cycle headline state is REBALANCE_WINDOW (§19.1/§19.3).
        if active_intents > 0:
            reasons.append(
                Reason(
                    code=REASON_ACTIVE_INTENT,
                    message=(
                        f"{active_intents} active deployment intent(s) are "
                        "queued to move buffer cash into the market."
                    ),
                    severity=SEVERITY_WARNING,
                )
            )
        for alert in ips_alerts:
            reasons.append(
                Reason(
                    code=REASON_IPS_VIOLATION,
                    message=alert.message,
                    severity=(
                        SEVERITY_CRITICAL
                        if alert.level == ips_enforcement.IpsEnforcementLevel.BLOCK.value
                        else SEVERITY_WARNING
                    ),
                )
            )
        for flag in severe_behavior_flags:
            reasons.append(
                Reason(
                    code=REASON_BEHAVIOR_FLAG,
                    message=flag.message,
                    severity=flag.severity,
                )
            )
        # Drift approaching but still within threshold (only when priced and
        # not already beyond — beyond-threshold drift surfaces via the IPS
        # alert above, and only forces REBALANCE_NOW inside a rebalance window).
        if approaching_drift and not drift_beyond:
            reasons.append(
                Reason(
                    code=REASON_DRIFT_APPROACHING,
                    message=(
                        f"Allocation drift {drift_max_pp}pp is approaching the "
                        f"{threshold}pp policy threshold (>70% of it)."
                    ),
                    severity=SEVERITY_WARNING,
                )
            )
        # An execution window is OPEN right now — review is most warranted when
        # the window is actually open, not only in the lead-up (§19.3). This is
        # the explicit "window open" reason that the lead-time WINDOW_SOON
        # trigger (below) cannot express once inside the window.
        if open_window:
            kind_label = (
                str(open_window_kind).lower()
                if open_window_kind is not None
                else "execution"
            )
            reasons.append(
                Reason(
                    code=REASON_WINDOW_OPEN,
                    message=(
                        f"An execution {kind_label} window is open now — review "
                        "your contribution / deployment and place the plan's "
                        "orders before it closes."
                    ),
                    severity=SEVERITY_INFO,
                )
            )
        # Next window within the review lead time (and still in the future).
        elif 0 <= days_to_window <= ips.review_lead_days:
            reasons.append(
                Reason(
                    code=REASON_WINDOW_SOON,
                    message=(
                        f"The next execution window opens on {next_window_date} "
                        f"(in {days_to_window} day(s)) — prepare your "
                        "contribution and review the plan."
                    ),
                    severity=SEVERITY_INFO,
                )
            )
        if draft_plans > 0:
            reasons.append(
                Reason(
                    code=REASON_DRAFT_PLAN,
                    message=(
                        f"{draft_plans} DRAFT execution plan(s) are awaiting "
                        "your approval."
                    ),
                    severity=SEVERITY_WARNING,
                )
            )

        if reasons:
            status = STATUS_REVIEW_REQUIRED
        else:
            # ---------------------------------------------------------- #
            # 3. DO_NOTHING (§19.3): the disciplined default — success.   #
            # ---------------------------------------------------------- #
            status = STATUS_DO_NOTHING
            reasons.append(
                Reason(
                    code=REASON_ON_TRACK,
                    message=(
                        "Accumulating within policy: no open window, no "
                        "deployable surplus over threshold, drift within "
                        "limits and no behavior or IPS flags."
                    ),
                    severity=SEVERITY_INFO,
                )
            )

    # Most-severe reasons first (stable within a severity band).
    reasons.sort(key=_severity_rank, reverse=True)

    headline, primary_action = _headline_and_action(
        status, reasons, deployable_myr, state.value
    )

    signals: dict[str, Any] = {
        "deployable_myr": str(deployable_myr),
        "max_drift_pp": str(drift_max_pp) if drift_known else None,
        "cash_drag_pp": str(cash_drag) if cash_drag is not None else None,
        "behavior_flag_count": len(severe_behavior_flags),
        "ips_violation_count": len(ips_alerts),
    }

    return ActionStatus(
        status=status,
        label=STATUS_LABELS[status],
        headline=headline,
        reasons=reasons,
        primary_action=primary_action,
        next_window_date=next_window_date,
        next_rebalance_date=(
            next_rebalance.isoformat() if next_rebalance is not None else None
        ),
        compliance_score=compliance_score,
        cycle_state=state.value,
        signals=signals,
        computed_at=_utc_now_iso(),
    )

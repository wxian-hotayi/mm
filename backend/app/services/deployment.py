"""Deployment queue — pending intents to move buffer cash into the market.

A :class:`~app.models.deployment.DeploymentIntent` is the operational record of
*intent to deploy*: it is enqueued automatically when deployable surplus first
crosses the threshold inside an open deployment window (``THRESHOLD``), raised
manually (``MANUAL``), or opened for a future window (``WINDOW``). It is later
attached to an :class:`~app.models.execution.ExecutionPlan` (``PLANNED``) and
marked ``EXECUTED`` once the cash leaves the buffer for the broker (DESIGN
§19.1).

Intent *status* is operational state, not derived portfolio state; cash
balances and deployable surplus remain derived from the movement ledger
(:mod:`app.services.cash`). Every mutation is audited and committed atomically.

Window dates: the canonical scheduler ``classify_window`` is owned by the
Unified Execution Window Engine (``services/execution.py``, §19.6). To stay
self-contained and avoid a circular dependency, this module derives the current
*open deployment window* directly from the same IPS config fields
(``execution_anchor_month``, ``deployment_interval_months``,
``execution_window_days``) using identical arithmetic — the single source of
truth for the schedule remains those config values.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.cash import CashMovement, CashMovementType
from app.models.deployment import (
    DeploymentIntent,
    DeploymentStatus,
    DeploymentTrigger,
)
from app.models.ips import IpsRule
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.services import cash
from app.utils.dates import kl_today
from app.utils.money import Q4, ZERO

# Intent statuses that count as an "open" (still-actionable) intent.
_OPEN_STATUSES: tuple[str, ...] = (
    DeploymentStatus.QUEUED.value,
    DeploymentStatus.PLANNED.value,
)


# --------------------------------------------------------------------------- #
# Audit helper                                                                 #
# --------------------------------------------------------------------------- #
def _audit(
    user_id: int,
    action: str,
    entity_id: int | None,
    description: str,
    context: dict[str, object],
) -> AuditLog:
    """Build an INFO ``AUDIT`` log row for a deployment-queue mutation."""
    return AuditLog(
        user_id=user_id,
        event_type=AuditEventType.AUDIT.value,
        action=action,
        severity=AuditSeverity.INFO.value,
        entity="deployment_intent",
        entity_id=str(entity_id) if entity_id is not None else None,
        description=description,
        context=json.dumps(context, default=str),
    )


# --------------------------------------------------------------------------- #
# IPS config + open-window derivation (mirrors §19.6 classify_window)          #
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


def _current_open_window_date(ips: IpsRule, today: date) -> date | None:
    """Return the open-date of the deployment window containing ``today``.

    Delegates to the single canonical scheduler
    (:func:`app.services.execution.current_open_window`, §19.6) so the schedule
    has exactly one source of truth — no duplicated arithmetic, no per-year
    cadence reset, and windows that opened in a prior month but are still open
    are correctly recognised. A late import avoids a circular dependency
    (``execution`` imports ``deployment``). Returns ``None`` when ``today`` is
    outside every window.
    """
    from app.services import execution  # late import: breaks import cycle

    window = execution.current_open_window(today, ips)
    return window.opens if window is not None else None


# --------------------------------------------------------------------------- #
# Queue queries                                                                #
# --------------------------------------------------------------------------- #
async def get_intent(
    db: AsyncSession, user: User, intent_id: int
) -> DeploymentIntent:
    """Fetch one of the user's deployment intents; raise NotFoundError."""
    result = await db.execute(
        select(DeploymentIntent).where(
            DeploymentIntent.id == intent_id,
            DeploymentIntent.user_id == user.id,
        )
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        raise NotFoundError(f"Deployment intent {intent_id} not found")
    return intent


async def list_intents(
    db: AsyncSession,
    user: User,
    status: DeploymentStatus | str | None = None,
) -> list[DeploymentIntent]:
    """List the user's deployment intents (newest first), optional status."""
    criteria = [DeploymentIntent.user_id == user.id]
    if status is not None:
        status_value = (
            status.value
            if isinstance(status, DeploymentStatus)
            else str(status)
        )
        if status_value not in {member.value for member in DeploymentStatus}:
            raise ValidationFailed(f"Unknown intent status {status_value!r}")
        criteria.append(DeploymentIntent.status == status_value)
    result = await db.execute(
        select(DeploymentIntent)
        .where(*criteria)
        .order_by(DeploymentIntent.created_at.desc(), DeploymentIntent.id.desc())
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Queue mutations                                                              #
# --------------------------------------------------------------------------- #
async def enqueue(
    db: AsyncSession,
    user: User,
    *,
    trigger: DeploymentTrigger | str,
    amount_myr: Decimal,
    source_account_id: int | None = None,
    target_window_date: date | None = None,
    notes: str = "",
) -> DeploymentIntent:
    """Enqueue a deployment intent (``QUEUED``), audit it and commit.

    ``amount_myr`` is the MYR buffer cash intended for deployment (must be
    positive). ``trigger`` records why it was queued (THRESHOLD/MANUAL/WINDOW).
    A ``source_account_id``, if given, must belong to the user.
    """
    trigger_value = (
        trigger.value if isinstance(trigger, DeploymentTrigger) else str(trigger)
    )
    if trigger_value not in {member.value for member in DeploymentTrigger}:
        raise ValidationFailed(f"Unknown deployment trigger {trigger_value!r}")
    if amount_myr <= ZERO:
        raise ValidationFailed("Deployment amount_myr must be positive")
    if source_account_id is not None:
        await cash.get_account(db, user, source_account_id)

    intent = DeploymentIntent(
        user_id=user.id,
        source_account_id=source_account_id,
        amount_myr=Q4(amount_myr),
        trigger=trigger_value,
        status=DeploymentStatus.QUEUED.value,
        target_window_date=target_window_date,
        notes=notes,
    )
    db.add(intent)
    await db.flush()
    db.add(
        _audit(
            user.id,
            "DEPLOYMENT_ENQUEUE",
            intent.id,
            f"Enqueued {trigger_value} deployment of {intent.amount_myr} MYR",
            {
                "trigger": trigger_value,
                "amount_myr": intent.amount_myr,
                "source_account_id": source_account_id,
                "target_window_date": target_window_date,
            },
        )
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def maybe_enqueue_threshold(
    db: AsyncSession, user: User, today: date | None = None
) -> DeploymentIntent | None:
    """Auto-enqueue a THRESHOLD intent when surplus crosses the threshold.

    Creates a ``THRESHOLD``/``QUEUED`` intent when, inside the *current open
    deployment window*, the user's deployable surplus first reaches
    ``min_deploy_threshold_myr`` (DESIGN §19.1). Idempotent: returns the
    existing open intent for the same window (no duplicate) and returns
    ``None`` when there is no open window or surplus is still below threshold.
    """
    today = today or kl_today()
    ips = await _load_ips(db, user)
    open_window = _current_open_window_date(ips, today)
    if open_window is None:
        return None
    surplus = await cash.deployable_surplus_myr(db, user, today)
    threshold = Q4(ips.min_deploy_threshold_myr)
    if surplus < threshold:
        return None

    # Idempotency: at most one open (QUEUED/PLANNED) THRESHOLD intent per
    # window. An intent without a target_window_date is treated as belonging to
    # the window it was raised in only when it is THRESHOLD-triggered.
    existing = await db.execute(
        select(DeploymentIntent).where(
            DeploymentIntent.user_id == user.id,
            DeploymentIntent.trigger == DeploymentTrigger.THRESHOLD.value,
            DeploymentIntent.status.in_(_OPEN_STATUSES),
            DeploymentIntent.target_window_date == open_window,
        )
    )
    open_intent = existing.scalar_one_or_none()
    if open_intent is not None:
        return open_intent

    return await enqueue(
        db,
        user,
        trigger=DeploymentTrigger.THRESHOLD,
        amount_myr=surplus,
        target_window_date=open_window,
        notes=(
            "Auto-queued: deployable surplus reached the deploy threshold "
            f"during the {open_window.isoformat()} window"
        ),
    )


async def attach_plan(
    db: AsyncSession,
    user: User,
    intent_id: int,
    execution_plan_id: int,
) -> DeploymentIntent:
    """Link an intent to an execution plan and mark it ``PLANNED``.

    The plan ownership is the execution engine's responsibility (§19.6); this
    function only records the linkage and advances the intent's lifecycle.
    """
    intent = await get_intent(db, user, intent_id)
    if intent.status not in _OPEN_STATUSES:
        raise ConflictError(
            f"Cannot attach a plan to a {intent.status} intent"
        )
    intent.execution_plan_id = execution_plan_id
    intent.status = DeploymentStatus.PLANNED.value
    db.add(
        _audit(
            user.id,
            "DEPLOYMENT_ATTACH_PLAN",
            intent.id,
            f"Attached execution plan {execution_plan_id} to intent "
            f"{intent.id}",
            {
                "execution_plan_id": execution_plan_id,
                "status": intent.status,
            },
        )
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def execute(
    db: AsyncSession,
    user: User,
    intent_id: int,
    *,
    emit_movement: bool = False,
    source_account_id: int | None = None,
    movement_date: date | None = None,
    fx_rate: Decimal | None = None,
    notes: str = "",
) -> DeploymentIntent:
    """Mark an intent ``EXECUTED`` and optionally emit the broker linkage.

    When ``emit_movement`` is set the function atomically (a) creates a broker
    ``DEPOSIT`` :class:`~app.models.transaction.Transaction` for ``amount_myr``
    (USD derived via ``fx_rate``) and (b) records a
    ``TRANSFER_OUT_TO_BROKER`` :class:`~app.models.cash.CashMovement` on the
    source buffer account, linking the movement to the deposit
    (``linked_transaction_id``). The deposit and movement are the only ledger
    side-effects — portfolio state stays derived.

    ``source_account_id`` defaults to the intent's ``source_account_id``;
    ``movement_date`` defaults to today (KL); ``fx_rate`` (USD->MYR) is required
    when emitting a movement so the deposit's USD amount is exact.
    """
    intent = await get_intent(db, user, intent_id)
    if intent.status == DeploymentStatus.EXECUTED.value:
        raise ConflictError(f"Intent {intent.id} is already executed")
    if intent.status == DeploymentStatus.CANCELLED.value:
        raise ConflictError(f"Cannot execute a cancelled intent {intent.id}")

    deposit: Transaction | None = None
    if emit_movement:
        account_id = source_account_id or intent.source_account_id
        if account_id is None:
            raise ValidationFailed(
                "A source_account_id is required to emit the broker movement"
            )
        if fx_rate is None or fx_rate <= ZERO:
            raise ValidationFailed(
                "A positive fx_rate (USD->MYR) is required to emit the deposit"
            )
        account = await cash.get_account(db, user, account_id)
        when = movement_date or kl_today()
        amount_myr = Q4(intent.amount_myr)

        # Broker DEPOSIT: MYR is authoritative; USD derived in the ledger
        # engine from total_amount_myr / fx_rate_recorded.
        deposit = Transaction(
            user_id=user.id,
            transaction_date=when,
            transaction_type=TransactionType.DEPOSIT.value,
            fee_usd=ZERO,
            fx_rate_recorded=Q4(fx_rate),
            total_amount_myr=amount_myr,
            notes=(
                notes
                or f"Broker deposit from deployment intent {intent.id}"
            ),
        )
        db.add(deposit)
        await db.flush()

        # Cash leaves the buffer toward the broker, linked to the deposit.
        await _emit_transfer_out(
            db,
            user_id=user.id,
            account_id=account.id,
            amount_myr=amount_myr,
            movement_date=when,
            linked_transaction_id=deposit.id,
            notes=(
                notes
                or f"Transfer to broker for deployment intent {intent.id}"
            ),
        )

    intent.status = DeploymentStatus.EXECUTED.value
    db.add(
        _audit(
            user.id,
            "DEPLOYMENT_EXECUTE",
            intent.id,
            f"Executed deployment intent {intent.id}",
            {
                "amount_myr": intent.amount_myr,
                "emitted_movement": emit_movement,
                "linked_transaction_id": deposit.id if deposit else None,
            },
        )
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def _emit_transfer_out(
    db: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    amount_myr: Decimal,
    movement_date: date,
    linked_transaction_id: int,
    notes: str,
) -> CashMovement:
    """Insert a ``TRANSFER_OUT_TO_BROKER`` movement linked to a broker deposit.

    Internal to :func:`execute`; does not commit (the caller commits the whole
    deposit+movement+intent unit atomically).
    """
    movement = CashMovement(
        user_id=user_id,
        account_id=account_id,
        movement_date=movement_date,
        movement_type=CashMovementType.TRANSFER_OUT_TO_BROKER.value,
        amount_myr=Q4(amount_myr),
        linked_transaction_id=linked_transaction_id,
        notes=notes,
    )
    db.add(movement)
    await db.flush()
    return movement


async def cancel(
    db: AsyncSession, user: User, intent_id: int
) -> DeploymentIntent:
    """Mark an intent ``CANCELLED``, audit and commit.

    Only open (QUEUED/PLANNED) intents may be cancelled; an executed intent
    represents committed ledger side-effects and cannot be reversed here.
    """
    intent = await get_intent(db, user, intent_id)
    if intent.status == DeploymentStatus.EXECUTED.value:
        raise ConflictError(f"Cannot cancel an executed intent {intent.id}")
    if intent.status == DeploymentStatus.CANCELLED.value:
        return intent
    intent.status = DeploymentStatus.CANCELLED.value
    db.add(
        _audit(
            user.id,
            "DEPLOYMENT_CANCEL",
            intent.id,
            f"Cancelled deployment intent {intent.id}",
            {"amount_myr": intent.amount_myr},
        )
    )
    await db.commit()
    await db.refresh(intent)
    return intent

"""Deployment queue (DESIGN §19.1) — enqueue, idempotent threshold, cancel,
execute with optional broker linkage.

Intent status is operational lifecycle state; cash balances and deployable
surplus stay derived from the movement ledger. The threshold-anchored window
math reuses the unified scheduler's config (anchor=3, deploy interval=3,
window_days=21 → Mar/Jun/Sep/Dec windows, each open day 1–21).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ConflictError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog
from app.models.cash import CashAccountType, CashMovement, CashMovementType
from app.models.deployment import DeploymentStatus, DeploymentTrigger
from app.models.execution import (
    ExecutionPlan,
    ExecutionPlanKind,
    ExecutionPlanStatus,
)
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.services import cash, deployment
from conftest import SeededCash, UserFactory, UserLoader
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_draft_plan(
    db_session, user: User, window_date: date
) -> ExecutionPlan:
    """Persist a minimal DRAFT execution plan to satisfy the intent FK link."""
    plan = ExecutionPlan(
        user_id=user.id,
        window_date=window_date,
        plan_kind=ExecutionPlanKind.DEPLOY.value,
        status=ExecutionPlanStatus.DRAFT.value,
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


# --------------------------------------------------------------------------- #
# enqueue                                                                      #
# --------------------------------------------------------------------------- #
async def test_enqueue_creates_queued_intent(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
        source_account_id=seeded_cash.buffer_account_id,
    )
    assert intent.status == DeploymentStatus.QUEUED.value
    assert intent.trigger == DeploymentTrigger.MANUAL.value
    assert intent.amount_myr == Decimal("1500.0000")
    listing = await deployment.list_intents(db_session, user)
    assert intent.id in {row.id for row in listing}


async def test_enqueue_rejects_non_positive_amount(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    with pytest.raises(ValidationFailed):
        await deployment.enqueue(
            db_session,
            user,
            trigger=DeploymentTrigger.MANUAL,
            amount_myr=Decimal("0"),
        )


# --------------------------------------------------------------------------- #
# idempotent threshold enqueue per open window                                 #
# --------------------------------------------------------------------------- #
async def test_threshold_enqueue_idempotent_per_window(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    # Surplus comfortably above the RM1,500 threshold.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("3000"),
        movement_date=date(2026, 3, 1),
    )
    # Inside the Mar window (Mar 1–21): first call queues a THRESHOLD intent.
    march_day = date(2026, 3, 10)
    first = await deployment.maybe_enqueue_threshold(
        db_session, user, today=march_day
    )
    assert first is not None
    assert first.trigger == DeploymentTrigger.THRESHOLD.value
    assert first.target_window_date == date(2026, 3, 1)
    assert first.amount_myr == Decimal("3000.0000")
    # A second call in the SAME window returns the existing intent (no dup).
    again = await deployment.maybe_enqueue_threshold(
        db_session, user, today=date(2026, 3, 15)
    )
    assert again is not None
    assert again.id == first.id
    # Exactly one THRESHOLD intent exists for this user.
    threshold_intents = [
        row
        for row in await deployment.list_intents(db_session, user)
        if row.trigger == DeploymentTrigger.THRESHOLD.value
    ]
    assert len(threshold_intents) == 1


async def test_threshold_enqueue_none_outside_window(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("3000"),
        movement_date=date(2026, 1, 5),
    )
    # April is not a window month -> no auto-enqueue even with ample surplus.
    assert (
        await deployment.maybe_enqueue_threshold(
            db_session, user, today=date(2026, 4, 10)
        )
        is None
    )
    # Mar 22 is one day past the close of the Mar window -> still no enqueue.
    assert (
        await deployment.maybe_enqueue_threshold(
            db_session, user, today=date(2026, 3, 22)
        )
        is None
    )


async def test_threshold_enqueue_none_below_threshold(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    # Surplus below the RM1,500 threshold, inside an open window.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 3, 1),
    )
    assert (
        await deployment.maybe_enqueue_threshold(
            db_session, user, today=date(2026, 3, 10)
        )
        is None
    )


# --------------------------------------------------------------------------- #
# cancel                                                                       #
# --------------------------------------------------------------------------- #
async def test_cancel_open_intent(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    cancelled = await deployment.cancel(db_session, user, intent.id)
    assert cancelled.status == DeploymentStatus.CANCELLED.value
    # Cancelling again is idempotent (stays CANCELLED, no error).
    again = await deployment.cancel(db_session, user, intent.id)
    assert again.status == DeploymentStatus.CANCELLED.value


async def test_cannot_cancel_executed_intent(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    await deployment.execute(db_session, user, intent.id)
    with pytest.raises(ConflictError):
        await deployment.cancel(db_session, user, intent.id)


# --------------------------------------------------------------------------- #
# execute: EXECUTED + optional broker linkage                                  #
# --------------------------------------------------------------------------- #
async def test_execute_marks_executed_without_movement(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    executed = await deployment.execute(db_session, user, intent.id)
    assert executed.status == DeploymentStatus.EXECUTED.value
    # Re-executing an executed intent is a conflict.
    with pytest.raises(ConflictError):
        await deployment.execute(db_session, user, intent.id)


async def test_execute_emits_broker_deposit_and_transfer(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("8900"),
        movement_date=date(2026, 1, 5),
    )
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("8900"),
        source_account_id=account.id,
    )
    executed = await deployment.execute(
        db_session,
        user,
        intent.id,
        emit_movement=True,
        fx_rate=Decimal("4.45"),
        movement_date=date(2026, 1, 6),
    )
    assert executed.status == DeploymentStatus.EXECUTED.value

    # A broker DEPOSIT for RM8,900 was created (MYR authoritative; USD derived
    # in the ledger engine as 8,900 / 4.45 = $2,000).
    deposits = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.transaction_type == TransactionType.DEPOSIT.value,
            )
        )
    ).scalars().all()
    assert len(deposits) == 1
    deposit = deposits[0]
    assert deposit.total_amount_myr == Decimal("8900.0000")
    assert deposit.fx_rate_recorded == Decimal("4.4500")

    # A TRANSFER_OUT_TO_BROKER movement linked to that deposit left the buffer.
    movements = (
        await db_session.execute(
            select(CashMovement).where(
                CashMovement.user_id == user.id,
                CashMovement.movement_type
                == CashMovementType.TRANSFER_OUT_TO_BROKER.value,
            )
        )
    ).scalars().all()
    assert len(movements) == 1
    assert movements[0].linked_transaction_id == deposit.id
    assert movements[0].amount_myr == Decimal("8900.0000")
    # The transfer-out drained the buffer (8,900 in − 8,900 out = RM0).
    assert await cash.balance(db_session, user, account.id) == Decimal(
        "0.0000"
    )


async def test_execute_emit_requires_fx_and_source(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    # No source account on the intent and none supplied -> ValidationFailed.
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    with pytest.raises(ValidationFailed):
        await deployment.execute(
            db_session,
            user,
            intent.id,
            emit_movement=True,
            fx_rate=Decimal("4.45"),
        )
    # Source present but no FX rate -> ValidationFailed.
    intent2 = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
        source_account_id=seeded_cash.buffer_account_id,
    )
    with pytest.raises(ValidationFailed):
        await deployment.execute(
            db_session,
            user,
            intent2.id,
            emit_movement=True,
        )


# --------------------------------------------------------------------------- #
# attach_plan lifecycle                                                        #
# --------------------------------------------------------------------------- #
async def test_attach_plan_marks_planned(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    # ``execution_plan_id`` carries a real FK to ``execution_plans.id``, so the
    # linkage must reference persisted plans (DRAFT is fine for the linkage).
    plan_a = await _make_draft_plan(db_session, user, date(2026, 3, 1))
    plan_b = await _make_draft_plan(db_session, user, date(2026, 3, 1))
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.WINDOW,
        amount_myr=Decimal("1500"),
        target_window_date=date(2026, 3, 1),
    )
    planned = await deployment.attach_plan(db_session, user, intent.id, plan_a.id)
    assert planned.status == DeploymentStatus.PLANNED.value
    assert planned.execution_plan_id == plan_a.id
    # Cannot re-attach to a non-open (here: cancelled) intent.
    await deployment.cancel(db_session, user, intent.id)
    with pytest.raises(ConflictError):
        await deployment.attach_plan(db_session, user, intent.id, plan_b.id)


async def test_deployment_mutations_are_audited(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    intent = await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.event_type == AuditEventType.AUDIT.value,
                AuditLog.entity == "deployment_intent",
                AuditLog.entity_id == str(intent.id),
            )
        )
    ).scalars().all()
    assert any(row.action == "DEPLOYMENT_ENQUEUE" for row in rows)

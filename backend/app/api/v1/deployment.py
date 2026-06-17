"""Deployment queue endpoints (DESIGN §19.1, §19.7).

Lists / enqueues deployment intents and runs the cancel / execute lifecycle
ops. JWT-guarded, per-user isolated; mutations are audited in the service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from app.core.deps import CurrentUser, DbDep
from app.models.deployment import DeploymentStatus
from app.schemas.deployment import (
    DeploymentExecuteIn,
    DeploymentIntentIn,
    DeploymentIntentOut,
)
from app.services import deployment

router = APIRouter(prefix="/deployment", tags=["deployment"])


@router.get("/queue", response_model=list[DeploymentIntentOut])
async def list_queue(
    db: DbDep,
    user: CurrentUser,
    intent_status: Annotated[
        DeploymentStatus | None, Query(alias="status")
    ] = None,
) -> list[DeploymentIntentOut]:
    """List the user's deployment intents (newest first), optional status."""
    intents = await deployment.list_intents(db, user, intent_status)
    return [DeploymentIntentOut.from_row(intent) for intent in intents]


@router.post(
    "/queue",
    response_model=DeploymentIntentOut,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_intent(
    payload: DeploymentIntentIn, db: DbDep, user: CurrentUser
) -> DeploymentIntentOut:
    """Enqueue a deployment intent (QUEUED) to move buffer cash to the market."""
    intent = await deployment.enqueue(
        db,
        user,
        trigger=payload.trigger,
        amount_myr=payload.amount_myr,
        source_account_id=payload.source_account_id,
        target_window_date=payload.target_window_date,
        notes=payload.notes,
    )
    return DeploymentIntentOut.from_row(intent)


@router.post("/{intent_id}/cancel", response_model=DeploymentIntentOut)
async def cancel_intent(
    intent_id: int, db: DbDep, user: CurrentUser
) -> DeploymentIntentOut:
    """Cancel an open (QUEUED/PLANNED) deployment intent."""
    intent = await deployment.cancel(db, user, intent_id)
    return DeploymentIntentOut.from_row(intent)


@router.post("/{intent_id}/execute", response_model=DeploymentIntentOut)
async def execute_intent(
    intent_id: int,
    db: DbDep,
    user: CurrentUser,
    payload: Annotated[DeploymentExecuteIn | None, Body()] = None,
) -> DeploymentIntentOut:
    """Mark a deployment intent EXECUTED, optionally emitting the broker
    deposit + ``TRANSFER_OUT_TO_BROKER`` cash movement (§19.1)."""
    body = payload or DeploymentExecuteIn()
    intent = await deployment.execute(
        db,
        user,
        intent_id,
        emit_movement=body.emit_movement,
        source_account_id=body.source_account_id,
        movement_date=body.movement_date,
        fx_rate=body.fx_rate,
        notes=body.notes,
    )
    return DeploymentIntentOut.from_row(intent)

"""Deployment queue request/response schemas (DESIGN §19.1, §19.7).

A deployment intent is the operational record of *intent to deploy* buffer cash
into the market. Money values are exact 4dp Decimals (Phase-1 ``MoneyIn`` /
``MoneyOut``); intent status/trigger are validated against the model enums.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.deployment import (
    DeploymentIntent,
    DeploymentStatus,
    DeploymentTrigger,
)
from app.schemas.common import MoneyOut, PositiveMoneyIn

_MAX_NOTES_LENGTH = 2000


class DeploymentIntentIn(BaseModel):
    """Enqueue payload for a deployment intent.

    ``trigger`` records why it was queued; ``amount_myr`` is the MYR buffer
    cash intended for deployment (must be positive). ``source_account_id``, if
    given, must belong to the user (validated in the service).
    """

    model_config = ConfigDict(extra="forbid")

    trigger: DeploymentTrigger = DeploymentTrigger.MANUAL
    amount_myr: PositiveMoneyIn
    source_account_id: int | None = Field(default=None, gt=0)
    target_window_date: date | None = None
    notes: str = Field(default="", max_length=_MAX_NOTES_LENGTH)


class DeploymentExecuteIn(BaseModel):
    """Optional body for ``POST /deployment/{id}/execute``.

    When ``emit_movement`` is set the service emits the matching broker
    ``DEPOSIT`` and a ``TRANSFER_OUT_TO_BROKER`` cash movement on the source
    account; ``source_account_id`` defaults to the intent's, ``movement_date``
    to today (KL), and a positive ``fx_rate`` (USD->MYR) is required.
    """

    model_config = ConfigDict(extra="forbid")

    emit_movement: bool = False
    source_account_id: int | None = Field(default=None, gt=0)
    movement_date: date | None = None
    fx_rate: PositiveMoneyIn | None = None
    notes: str = Field(default="", max_length=_MAX_NOTES_LENGTH)


class DeploymentIntentOut(BaseModel):
    """A queued deployment intent as stored."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_account_id: int | None
    amount_myr: MoneyOut
    trigger: str
    status: str
    target_window_date: date | None
    execution_plan_id: int | None
    notes: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, intent: DeploymentIntent) -> "DeploymentIntentOut":
        return cls.model_validate(intent)

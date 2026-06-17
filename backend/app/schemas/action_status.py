"""Action Status response schema (DESIGN §19.3, §19.7) — the primary signal.

Mirrors :class:`app.services.action_status.ActionStatus` exactly. The status is
one of ``DO_NOTHING | REVIEW_REQUIRED | REBALANCE_NOW`` with display labels
"Do Nothing" / "Review" / "Rebalance Now". ``signals`` carries Decimal figures
as strings (``max_drift_pp`` / ``cash_drag_pp`` may be ``None`` when unpriced).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.action_status import ActionStatus


class ReasonOut(BaseModel):
    """One driver behind the Action Status decision (``{code, message, severity}``)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    severity: str


class ActionStatusOut(BaseModel):
    """The single system-wide Action Status decision (mirrors ``ActionStatus``)."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    label: str
    headline: str
    reasons: list[ReasonOut]
    primary_action: str
    next_window_date: str
    next_rebalance_date: str | None
    compliance_score: int
    cycle_state: str
    signals: dict[str, Any]
    computed_at: str

    @classmethod
    def from_status(cls, status: ActionStatus) -> "ActionStatusOut":
        return cls.model_validate(status)

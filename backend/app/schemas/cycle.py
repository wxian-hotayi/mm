"""Wealth Operating Cycle response schema (DESIGN §19.2, §19.7).

The current life-cycle state is derived (never stored authoritatively); the
``context`` mapping is the JSON signal snapshot produced by
:func:`app.services.cycle.current_state`. ``recent_transitions`` exposes the
``cycle_state_log`` history rows (reporting only).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.cycle import CycleStateLog
from app.services.cycle import CycleState


class CycleTransitionOut(BaseModel):
    """One logged wealth-cycle state transition (history/reporting only)."""

    id: int
    state: str
    entered_at: datetime
    context: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: CycleStateLog) -> "CycleTransitionOut":
        try:
            context = json.loads(row.context) if row.context else {}
        except json.JSONDecodeError:
            context = {}
        if not isinstance(context, dict):
            context = {}
        return cls(
            id=row.id,
            state=row.state,
            entered_at=row.entered_at,
            context=context,
            created_at=row.created_at,
        )


class CycleStateOut(BaseModel):
    """The derived current cycle state plus recent transition history (§19.2).

    ``state`` is a :class:`~app.models.cycle.WealthCycleState` value; ``since``
    is when it was entered; ``context`` is the signal snapshot consumed by the
    Action Status engine (deployable cash, drift, window info, intents/plans).
    """

    state: str
    since: datetime
    context: dict[str, Any]
    recent_transitions: list[CycleTransitionOut]

    @classmethod
    def from_state(
        cls,
        cycle_state: CycleState,
        transitions: list[CycleStateLog],
    ) -> "CycleStateOut":
        return cls(
            state=cycle_state.state.value,
            since=cycle_state.since,
            context=cycle_state.context,
            recent_transitions=[
                CycleTransitionOut.from_row(row) for row in transitions
            ],
        )

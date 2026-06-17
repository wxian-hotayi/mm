"""Wealth Operating Cycle endpoint (DESIGN §19.2, §19.7).

Returns the **derived** current life-cycle state plus recent logged transitions
(history only). JWT-guarded, per-user. Optional ``prices`` + ``fx_rate`` enable
the drift signal; without them drift is reported as unknown.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.deps import CurrentUser, DbDep
from app.models.cycle import CycleStateLog
from app.schemas.cycle import CycleStateOut
from app.services import cycle
from app.utils.money import D

router = APIRouter(prefix="/cycle", tags=["cycle"])

_RECENT_TRANSITION_LIMIT = 10


def _prices_from_query(raw: list[str] | None) -> dict[str, Decimal] | None:
    """Parse repeated ``price=SYMBOL:VALUE`` query params into a price map."""
    if not raw:
        return None
    prices: dict[str, Decimal] = {}
    for item in raw:
        symbol, _, value = item.partition(":")
        symbol = symbol.strip().upper()
        if not symbol or not value.strip():
            continue
        prices[symbol] = D(value.strip())
    return prices or None


@router.get("/state", response_model=CycleStateOut)
async def cycle_state(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
) -> CycleStateOut:
    """Derive the current wealth operating-cycle state (§19.2) and return it
    with the most recent transition log rows (reporting only). Read-only — the
    cycle log is written by mutation paths, never by this query."""
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    state = await cycle.current_state(
        db, user, prices=prices, fx_rate=fx, today=as_of, log=False
    )
    result = await db.execute(
        select(CycleStateLog)
        .where(CycleStateLog.user_id == user.id)
        .order_by(CycleStateLog.entered_at.desc(), CycleStateLog.id.desc())
        .limit(_RECENT_TRANSITION_LIMIT)
    )
    transitions = list(result.scalars().all())
    return CycleStateOut.from_state(state, transitions)

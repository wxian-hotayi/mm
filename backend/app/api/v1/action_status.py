"""Action Status endpoint (DESIGN §19.3, §19.7) — the primary dashboard signal.

Returns the single system-wide decision ``DO_NOTHING | REVIEW_REQUIRED |
REBALANCE_NOW``. Pure read, JWT-guarded, per-user. Optional ``prices`` +
``fx_rate`` enable the drift / cash-drag reasons; without them those reasons are
skipped cleanly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbDep
from app.schemas.action_status import ActionStatusOut
from app.services import action_status
from app.utils.money import D

router = APIRouter(tags=["action-status"])


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


@router.get("/action-status", response_model=ActionStatusOut)
async def get_action_status(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
) -> ActionStatusOut:
    """Compute the Action Status (§19.3): the single most important output of
    WealthOS. Supply ``price=SYMBOL:USD`` params and ``fx_rate`` (USD->MYR) to
    include drift / cash-drag reasons."""
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    status = await action_status.compute(
        db, user, prices=prices, fx_rate=fx, today=as_of
    )
    return ActionStatusOut.from_status(status)

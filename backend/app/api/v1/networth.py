"""Net Worth reporting endpoints (DESIGN §19.4, §19.7).

Net Worth is a reporting aggregate that references (never mutates) the
operational cash system and the portfolio ledger — portfolio is a subset.
JWT-guarded, per-user isolated, read-only. Optional ``prices`` + ``fx_rate``
price the live investment leg; when omitted it is reported as zero with
``portfolio.priced = False`` (never crashes).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbDep
from app.schemas.networth import (
    NetWorthBreakdownOut,
    NetWorthQueryIn,
    NetWorthSummaryOut,
)
from app.services import networth
from app.utils.money import D

router = APIRouter(prefix="/networth", tags=["networth"])


def _prices_from_query(
    raw: list[str] | None,
) -> dict[str, Decimal] | None:
    """Parse repeated ``price=SYMBOL:VALUE`` query params into a price map.

    Each entry is ``SYMBOL:USD_PRICE`` (e.g. ``VOO:500.25``); values are parsed
    exactly via :func:`app.utils.money.D` (no float). Returns ``None`` when no
    prices were supplied so the investment leg degrades gracefully (§19.4).
    """
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


@router.get("/summary", response_model=NetWorthSummaryOut)
async def networth_summary(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
) -> NetWorthSummaryOut:
    """Expanded Net Worth aggregate: investment + cash + assets − liabilities,
    a category breakdown, the portfolio subset and deployable surplus (§19.4).

    Supply prices as repeated ``price=SYMBOL:USD`` params and ``fx_rate`` as the
    USD->MYR rate to value the live investment leg.
    """
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    summary = await networth.summary(db, user, prices=prices, fx_rate=fx, as_of=as_of)
    return NetWorthSummaryOut.from_summary(summary)


@router.post("/summary", response_model=NetWorthSummaryOut)
async def networth_summary_post(
    payload: NetWorthQueryIn, db: DbDep, user: CurrentUser
) -> NetWorthSummaryOut:
    """Net Worth aggregate with a JSON pricing body (exact Decimal prices)."""
    summary = await networth.summary(
        db,
        user,
        prices=payload.prices,
        fx_rate=payload.fx_rate,
        as_of=payload.as_of,
    )
    return NetWorthSummaryOut.from_summary(summary)


@router.get("/breakdown", response_model=NetWorthBreakdownOut)
async def networth_breakdown(
    db: DbDep,
    user: CurrentUser,
    price: Annotated[list[str] | None, Query()] = None,
    fx_rate: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
) -> NetWorthBreakdownOut:
    """Net Worth category breakdown only (INVESTMENT/CASH/.../LIABILITY)."""
    prices = _prices_from_query(price)
    fx = D(fx_rate) if fx_rate is not None else None
    summary = await networth.summary(db, user, prices=prices, fx_rate=fx, as_of=as_of)
    return NetWorthBreakdownOut.from_summary(summary)

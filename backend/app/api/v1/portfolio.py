"""Portfolio endpoints: on-demand valuation and rebalance planning.

Both endpoints replay the user's transaction ledger on demand (ledger-first:
no derived state is stored) and price it with caller-supplied USD prices and
the USD->MYR FX rate. Missing prices for held or target symbols produce a
422 listing every offending symbol.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.transactions import load_user_transactions
from app.core.deps import CurrentUser, DbDep
from app.core.errors import NotFoundError
from app.models.ips import IpsRule
from app.schemas.portfolio import (
    RebalanceIn,
    RebalanceOrderOut,
    RebalanceOut,
    ValuationIn,
    ValuationOut,
)
from app.services.ledger import replay
from app.services.rebalance import plan_rebalance
from app.services.valuation import Valuation, valuation

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def _load_ips(db: AsyncSession, user_id: int) -> IpsRule:
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user_id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(
            "No Investment Policy Statement found for this user"
        )
    return ips


def _current_weights(snapshot: Valuation) -> dict[str, Decimal]:
    weights: dict[str, Decimal] = {
        holding.symbol: holding.weight_pct
        for holding in snapshot.holdings
        if holding.weight_pct is not None
    }
    if snapshot.cash_weight_pct is not None:
        weights["CASH"] = snapshot.cash_weight_pct
    return weights


@router.post("/valuation", response_model=ValuationOut)
async def portfolio_valuation(
    payload: ValuationIn, db: DbDep, user: CurrentUser
) -> ValuationOut:
    """Replay the ledger (optionally as of a date) and price it."""
    transactions = await load_user_transactions(db, user.id)
    state = replay(transactions, as_of=payload.as_of)
    snapshot = valuation(state, payload.prices, payload.fx_rate)
    return ValuationOut.model_validate(snapshot)


@router.post("/rebalance", response_model=RebalanceOut)
async def portfolio_rebalance(
    payload: RebalanceIn, db: DbDep, user: CurrentUser
) -> RebalanceOut:
    """Build a rebalance plan against the user's IPS policy (cash first,
    then the planned contribution, selling only as a last resort)."""
    transactions = await load_user_transactions(db, user.id)
    ips = await _load_ips(db, user.id)
    state = replay(transactions)
    plan = plan_rebalance(
        state,
        payload.prices,
        payload.fx_rate,
        ips,
        extra_cash_usd=payload.additional_cash_usd,
        threshold_pct=payload.threshold_pct,
    )
    snapshot = valuation(state, payload.prices, payload.fx_rate)
    return RebalanceOut(
        status=plan.status.value,
        orders=[
            RebalanceOrderOut.model_validate(order) for order in plan.orders
        ],
        steps=plan.steps,
        current_weights=_current_weights(snapshot),
        post_trade_weights=plan.post_trade_weights_pct,
        leftover_cash_usd=plan.leftover_cash_usd,
        max_drift_pp=plan.max_abs_drift_pp,
        priority_note=plan.priority_note,
        message=plan.message,
    )

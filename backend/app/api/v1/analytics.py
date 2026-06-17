"""Analytics endpoints: the behavior-protection report.

The report is read-only: flags are computed live from the ledger and the
IPS policy without writing audit rows (recording happens on transaction
mutations). Price-dependent flags (allocation drift, excessive cash) are
included only when ``fx_rate`` — and prices for every held symbol — are
supplied as query parameters.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.transactions import load_user_transactions
from app.core.deps import CurrentUser, DbDep
from app.core.errors import NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog
from app.models.ips import IpsRule
from app.models.transaction import Transaction, TransactionType
from app.schemas.behavior import (
    BehaviorFlagOut,
    BehaviorHistoryOut,
    BehaviorReportOut,
    TradeStatsOut,
)
from app.services.behavior import compute_flags
from app.utils.dates import in_rolling_window, kl_today
from app.utils.money import Q4, ZERO

router = APIRouter(prefix="/analytics", tags=["analytics"])

_TRADE_TYPE_VALUES = frozenset(
    {TransactionType.BUY.value, TransactionType.SELL.value}
)
_STATS_LOOKBACK_DAYS = 30
_STATS_WINDOW_DAYS = 7
_HISTORY_LIMIT = 20


def _trade_stats(
    transactions: list[Transaction], today: date
) -> TradeStatsOut:
    recent = [
        txn
        for txn in transactions
        if txn.transaction_type in _TRADE_TYPE_VALUES
        and in_rolling_window(
            txn.transaction_date, today, _STATS_LOOKBACK_DAYS
        )
    ]
    trade_dates = sorted(txn.transaction_date for txn in recent)
    max_in_window = 0
    for index, window_start in enumerate(trade_dates):
        count = sum(
            1
            for d in trade_dates[index:]
            if (d - window_start).days < _STATS_WINDOW_DAYS
        )
        max_in_window = max(max_in_window, count)
    return TradeStatsOut(
        trades_30d=len(recent),
        buys_30d=sum(
            1
            for txn in recent
            if txn.transaction_type == TransactionType.BUY.value
        ),
        sells_30d=sum(
            1
            for txn in recent
            if txn.transaction_type == TransactionType.SELL.value
        ),
        max_trades_in_7d=max_in_window,
    )


def _history_item(row: AuditLog) -> BehaviorHistoryOut:
    try:
        context = json.loads(row.context)
    except (json.JSONDecodeError, TypeError):
        context = {}
    title = context.get("title") if isinstance(context, dict) else None
    return BehaviorHistoryOut(
        code=row.action,
        severity=row.severity,
        title=title if isinstance(title, str) else "",
        message=row.description,
        created_at=row.created_at,
    )


async def _recent_history(
    db: AsyncSession, user_id: int
) -> list[BehaviorHistoryOut]:
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.event_type == AuditEventType.BEHAVIOR_FLAG.value,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(_HISTORY_LIMIT)
    )
    return [_history_item(row) for row in result.scalars().all()]


def _validated_prices(
    fx_rate: Decimal | None,
    voo_price: Decimal | None,
    qqq_price: Decimal | None,
) -> tuple[dict[str, Decimal] | None, Decimal | None]:
    """Normalize the optional pricing query parameters.

    Returns ``(prices, fx_rate)`` ready for the behavior engine, or
    ``(None, None)`` when pricing was not requested. Prices without an FX
    rate are rejected; supplied values must be positive.
    """
    supplied = {
        symbol: price
        for symbol, price in (("VOO", voo_price), ("QQQ", qqq_price))
        if price is not None
    }
    if fx_rate is None:
        if supplied:
            raise ValidationFailed(
                "fx_rate is required when voo_price or qqq_price is provided"
            )
        return None, None
    if fx_rate <= ZERO:
        raise ValidationFailed("fx_rate must be positive")
    non_positive = sorted(
        symbol for symbol, price in supplied.items() if price <= ZERO
    )
    if non_positive:
        raise ValidationFailed(
            "Price must be positive for: " + ", ".join(non_positive)
        )
    prices = {symbol: Q4(price) for symbol, price in supplied.items()}
    return prices, Q4(fx_rate)


@router.get("/behavior", response_model=BehaviorReportOut)
async def behavior_report(
    db: DbDep,
    user: CurrentUser,
    fx_rate: Annotated[
        Decimal | None,
        Query(description="USD->MYR rate enabling drift/cash flags"),
    ] = None,
    voo_price: Annotated[
        Decimal | None, Query(description="VOO price in USD")
    ] = None,
    qqq_price: Annotated[
        Decimal | None, Query(description="QQQ price in USD")
    ] = None,
) -> BehaviorReportOut:
    """Behavior-protection report: live flags, trade statistics over the
    trailing 30 days and recently recorded flags from the audit log."""
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(
            "No Investment Policy Statement found for this user"
        )
    transactions = await load_user_transactions(db, user.id)
    prices, effective_fx = _validated_prices(fx_rate, voo_price, qqq_price)
    flags = compute_flags(
        transactions, ips, prices=prices, fx_rate=effective_fx
    )
    return BehaviorReportOut(
        flags=[BehaviorFlagOut.model_validate(flag) for flag in flags],
        trade_stats=_trade_stats(transactions, kl_today()),
        recent_history=await _recent_history(db, user.id),
        generated_at=datetime.now(timezone.utc),
    )

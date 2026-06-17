"""Behavior-protection engine: deterministic discipline flags over the ledger.

Flag computation is pure (:func:`compute_flags`); persistence writes each
flag as an ``audit_logs`` row with ``event_type='BEHAVIOR_FLAG'``, deduped
per (flag code, KL calendar day). The session is flushed but not committed —
the request lifecycle owns the commit.

Flag codes: HIGH_FREQUENCY_TRADING, FORBIDDEN_ASSET, ALLOCATION_DRIFT,
EXCESSIVE_CASH, CONTRIBUTION_GAP, EARLY_SELL.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.ips import IpsRule
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.services.drift import drift
from app.services.ledger import replay
from app.services.valuation import valuation
from app.utils.dates import (
    add_months,
    in_rolling_window,
    kl_today,
    rolling_window_start,
    utc_to_kl_date,
)

HIGH_FREQUENCY_TRADING: Final[str] = "HIGH_FREQUENCY_TRADING"
FORBIDDEN_ASSET: Final[str] = "FORBIDDEN_ASSET"
ALLOCATION_DRIFT: Final[str] = "ALLOCATION_DRIFT"
EXCESSIVE_CASH: Final[str] = "EXCESSIVE_CASH"
CONTRIBUTION_GAP: Final[str] = "CONTRIBUTION_GAP"
EARLY_SELL: Final[str] = "EARLY_SELL"

_TRADE_TYPES: Final[frozenset[str]] = frozenset(
    {TransactionType.BUY.value, TransactionType.SELL.value}
)
_HFT_LOOKBACK_DAYS: Final[int] = 30
_HFT_WINDOW_DAYS: Final[int] = 7
_HFT_TRADE_THRESHOLD: Final[int] = 3
_CONTRIBUTION_GAP_DAYS: Final[int] = 45
_DEDUPE_LOOKBACK_DAYS: Final[int] = 2


@dataclass(frozen=True)
class BehaviorFlag:
    """One deterministic discipline flag.

    ``severity`` is an :class:`~app.models.audit.AuditSeverity` value;
    ``evidence`` is a JSON-serializable mapping (dates as ISO strings,
    Decimals as strings).
    """

    code: str
    severity: str
    title: str
    message: str
    evidence: dict[str, object]


def _parse_allowed_symbols(raw: str) -> set[str]:
    """Parse the IPS ``allowed_symbols`` JSON array into uppercase symbols."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailed(
            "IPS allowed_symbols is not valid JSON"
        ) from exc
    if not isinstance(parsed, list) or not all(
        isinstance(symbol, str) for symbol in parsed
    ):
        raise ValidationFailed(
            "IPS allowed_symbols must be a JSON array of symbol strings"
        )
    return {symbol.upper() for symbol in parsed}


def _high_frequency_flag(
    transactions: Sequence[Transaction], today: date
) -> BehaviorFlag | None:
    """>= 3 BUY/SELL trades inside any rolling 7-day window over the trailing
    30 days (WARNING)."""
    trade_dates = sorted(
        txn.transaction_date
        for txn in transactions
        if txn.transaction_type in _TRADE_TYPES
        and in_rolling_window(txn.transaction_date, today, _HFT_LOOKBACK_DAYS)
    )
    violating: set[date] = set()
    max_window_count = 0
    for index, window_start in enumerate(trade_dates):
        window = [
            d
            for d in trade_dates[index:]
            if (d - window_start).days < _HFT_WINDOW_DAYS
        ]
        if len(window) >= _HFT_TRADE_THRESHOLD:
            violating.update(window)
            max_window_count = max(max_window_count, len(window))
    if not violating:
        return None
    return BehaviorFlag(
        code=HIGH_FREQUENCY_TRADING,
        severity=AuditSeverity.WARNING.value,
        title="High-frequency trading detected",
        message=(
            f"{max_window_count} BUY/SELL trades within a rolling "
            f"{_HFT_WINDOW_DAYS}-day window in the last "
            f"{_HFT_LOOKBACK_DAYS} days. The policy is buy-and-hold with "
            "scheduled rebalancing — frequent trading erodes returns."
        ),
        evidence={
            "trade_dates": sorted(d.isoformat() for d in violating),
            "window_days": _HFT_WINDOW_DAYS,
            "threshold": _HFT_TRADE_THRESHOLD,
        },
    )


def _forbidden_asset_flag(
    transactions: Sequence[Transaction],
    held_symbols: set[str],
    ips: IpsRule,
) -> BehaviorFlag | None:
    """Held or traded symbol outside the IPS allowed list (CRITICAL)."""
    allowed = _parse_allowed_symbols(ips.allowed_symbols)
    traded = {
        txn.asset_symbol.upper()
        for txn in transactions
        if txn.transaction_type in _TRADE_TYPES and txn.asset_symbol
    }
    forbidden = sorted(
        (traded | {symbol.upper() for symbol in held_symbols}) - allowed
    )
    if not forbidden:
        return None
    return BehaviorFlag(
        code=FORBIDDEN_ASSET,
        severity=AuditSeverity.CRITICAL.value,
        title="Forbidden asset in portfolio",
        message=(
            f"Symbols outside the IPS allowed list: {', '.join(forbidden)}. "
            f"The policy permits only: {', '.join(sorted(allowed))}."
        ),
        evidence={
            "forbidden_symbols": forbidden,
            "allowed_symbols": sorted(allowed),
        },
    )


def _contribution_gap_flag(
    transactions: Sequence[Transaction], today: date
) -> BehaviorFlag | None:
    """No DEPOSIT inside the trailing 45-day window (INFO)."""
    deposit_dates = [
        txn.transaction_date
        for txn in transactions
        if txn.transaction_type == TransactionType.DEPOSIT.value
    ]
    last_deposit = max(deposit_dates) if deposit_dates else None
    window_start = rolling_window_start(today, _CONTRIBUTION_GAP_DAYS)
    if last_deposit is not None and last_deposit >= window_start:
        return None
    gap_days = (today - last_deposit).days if last_deposit else None
    if last_deposit is None:
        detail = "No deposit has ever been recorded."
    else:
        detail = (
            f"Last deposit was {gap_days} days ago "
            f"({last_deposit.isoformat()})."
        )
    return BehaviorFlag(
        code=CONTRIBUTION_GAP,
        severity=AuditSeverity.INFO.value,
        title="Contribution gap",
        message=(
            f"No deposit in the last {_CONTRIBUTION_GAP_DAYS} days. {detail} "
            "Consistency beats timing — keep the monthly contribution going."
        ),
        evidence={
            "last_deposit_date": (
                last_deposit.isoformat() if last_deposit else None
            ),
            "gap_days": gap_days,
            "window_days": _CONTRIBUTION_GAP_DAYS,
        },
    )


def _early_sell_flag(
    transactions: Sequence[Transaction], ips: IpsRule
) -> BehaviorFlag | None:
    """SELL within ``min_holding_period_years`` of the symbol's first BUY
    (WARNING; informational, never blocking)."""
    ordered = sorted(
        transactions,
        key=lambda txn: (txn.transaction_date, txn.id is None, txn.id or 0),
    )
    first_buy: dict[str, date] = {}
    for txn in ordered:
        if txn.transaction_type == TransactionType.BUY.value and (
            txn.asset_symbol
        ):
            first_buy.setdefault(txn.asset_symbol, txn.transaction_date)
    events: list[dict[str, object]] = []
    for txn in ordered:
        if txn.transaction_type != TransactionType.SELL.value or (
            not txn.asset_symbol
        ):
            continue
        bought = first_buy.get(txn.asset_symbol)
        if bought is None:
            continue
        holding_period_ends = add_months(
            bought, ips.min_holding_period_years * 12
        )
        if txn.transaction_date < holding_period_ends:
            events.append(
                {
                    "symbol": txn.asset_symbol,
                    "sell_date": txn.transaction_date.isoformat(),
                    "first_buy_date": bought.isoformat(),
                    "holding_period_ends": holding_period_ends.isoformat(),
                }
            )
    if not events:
        return None
    return BehaviorFlag(
        code=EARLY_SELL,
        severity=AuditSeverity.WARNING.value,
        title="Sell before minimum holding period",
        message=(
            f"{len(events)} sell(s) occurred before the IPS minimum holding "
            f"period of {ips.min_holding_period_years} years from the first "
            "buy. Long-term compounding requires holding through cycles."
        ),
        evidence={
            "events": events,
            "min_holding_period_years": ips.min_holding_period_years,
        },
    )


def compute_flags(
    transactions: Sequence[Transaction],
    ips: IpsRule,
    *,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
    today: date | None = None,
) -> list[BehaviorFlag]:
    """Compute all behavior flags for a user's ledger (pure given inputs).

    ``prices`` (USD per held symbol) and ``fx_rate`` (USD->MYR) enable the
    valuation-dependent flags (ALLOCATION_DRIFT, EXCESSIVE_CASH); when either
    is omitted those flags are skipped cleanly. ``today`` defaults to today
    in Asia/Kuala_Lumpur.
    """
    if not transactions:
        return []
    reference_date = today if today is not None else kl_today()
    state = replay(transactions)
    flags: list[BehaviorFlag] = []

    hft = _high_frequency_flag(transactions, reference_date)
    if hft is not None:
        flags.append(hft)

    forbidden = _forbidden_asset_flag(
        transactions, set(state.positions), ips
    )
    if forbidden is not None:
        flags.append(forbidden)

    if prices is not None and fx_rate is not None:
        current = valuation(state, prices, fx_rate)
        report = drift(current, ips)
        if not report.within_threshold:
            flags.append(
                BehaviorFlag(
                    code=ALLOCATION_DRIFT,
                    severity=AuditSeverity.WARNING.value,
                    title="Allocation drift beyond threshold",
                    message=(
                        f"Max drift {report.max_abs_drift_pp}pp exceeds the "
                        f"{ips.drift_threshold_pct}pp IPS threshold. Review "
                        "the rebalance plan instead of trading ad hoc."
                    ),
                    evidence={
                        "max_abs_drift_pp": str(report.max_abs_drift_pp),
                        "threshold_pp": str(ips.drift_threshold_pct),
                        "drift_pp_by_symbol": {
                            item.symbol: str(item.drift_pp)
                            for item in report.items
                        },
                    },
                )
            )
        if (
            current.cash_weight_pct is not None
            and current.cash_weight_pct > ips.max_cash_drag_pct
        ):
            flags.append(
                BehaviorFlag(
                    code=EXCESSIVE_CASH,
                    severity=AuditSeverity.WARNING.value,
                    title="Excessive idle cash",
                    message=(
                        f"Cash is {current.cash_weight_pct}% of the "
                        f"portfolio, above the {ips.max_cash_drag_pct}% "
                        "policy maximum. Idle cash drags long-term returns."
                    ),
                    evidence={
                        "cash_weight_pct": str(current.cash_weight_pct),
                        "max_cash_drag_pct": str(ips.max_cash_drag_pct),
                    },
                )
            )

    gap = _contribution_gap_flag(transactions, reference_date)
    if gap is not None:
        flags.append(gap)

    early = _early_sell_flag(transactions, ips)
    if early is not None:
        flags.append(early)

    return flags


async def record_flags(
    db: AsyncSession, user_id: int, flags: Sequence[BehaviorFlag]
) -> int:
    """Persist flags as ``AuditLog(event_type='BEHAVIOR_FLAG')`` rows, deduped
    per (flag code, KL calendar day). Flushes (does not commit) and returns
    the number of newly inserted rows."""
    if not flags:
        return 0
    today_kl = kl_today()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=_DEDUPE_LOOKBACK_DAYS
    )
    codes = sorted({flag.code for flag in flags})
    result = await db.execute(
        select(AuditLog.action, AuditLog.created_at).where(
            AuditLog.user_id == user_id,
            AuditLog.event_type == AuditEventType.BEHAVIOR_FLAG.value,
            AuditLog.action.in_(codes),
            AuditLog.created_at >= cutoff,
        )
    )
    existing = {
        (action, utc_to_kl_date(created_at))
        for action, created_at in result.all()
    }
    inserted = 0
    for flag in flags:
        if (flag.code, today_kl) in existing:
            continue
        db.add(
            AuditLog(
                user_id=user_id,
                event_type=AuditEventType.BEHAVIOR_FLAG.value,
                action=flag.code,
                severity=flag.severity,
                entity="behavior",
                description=flag.message,
                context=json.dumps(
                    {"title": flag.title, "evidence": flag.evidence},
                    default=str,
                ),
            )
        )
        existing.add((flag.code, today_kl))
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


async def evaluate_and_record(
    db: AsyncSession,
    user: User,
    transactions: Sequence[Transaction],
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
) -> list[BehaviorFlag]:
    """Compute all behavior flags for ``user`` against their IPS policy and
    persist them to the audit log (deduped per code per KL day).

    Loads the user's single :class:`IpsRule` row; raises
    :class:`NotFoundError` when none exists. Returns every computed flag,
    including those already recorded today.
    """
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(f"No IPS policy found for user {user.id}")
    flags = compute_flags(
        transactions, ips, prices=prices, fx_rate=fx_rate
    )
    await record_flags(db, user.id, flags)
    return flags

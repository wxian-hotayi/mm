"""Net Worth Engine expansion (DESIGN §19.4) — Portfolio ⊂ Net Worth.

Net Worth is the reporting aggregate:

    net_worth = investment + cash + other_assets − liabilities

where ``investment`` is the live portfolio subset (NAV USD × FX), ``cash`` is a
read-only reporting view of operational balances, ``other_assets``/``liabilities``
come from manual ``net_worth_entries`` (de-duplicated against operational cash so
the same ringgit is never double-counted), and snapshots capture cash for history.

These tests assert exact 4dp MYR aggregation with hand-computed comments, that
liabilities subtract, that operational cash is counted exactly once (no
double-count with a manual CASH entry), and that snapshot capture upserts. Each
test uses its own isolated user.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.models.net_worth import NetWorthCategory, NetWorthEntry
from app.models.networth_snapshot import NetWorthCashSnapshot
from app.services import cash, networth
from conftest import UserFactory, UserLoader
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

_FX = Decimal("4.45")
_PRICES = {"VOO": Decimal("100"), "QQQ": Decimal("100")}


async def _seed_portfolio(db_session, user) -> None:
    """Deposit $1,000 and buy 7 VOO + 3 QQQ ($1,000 total) -> NAV $1,000.

    NAV USD = 7×100 + 3×100 + cash $0 = $1,000.00. investment_myr = 1,000 ×
    4.45 = RM4,450.0000.
    """
    from app.models.transaction import Transaction

    db_session.add_all(
        [
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 5),
                transaction_type="DEPOSIT",
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("4450.0000"),
                fee_usd=Decimal("0"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="VOO",
                quantity=Decimal("7.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("3115.0000"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="QQQ",
                quantity=Decimal("3.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("1335.0000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()


async def _gxbank(db_session, user, amount: Decimal) -> int:
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=amount,
        movement_date=date(2026, 1, 5),
    )
    return account.id


def _breakdown(summary: networth.NetWorthSummary) -> dict[str, Decimal]:
    return {item.category: item.amount_myr for item in summary.breakdown}


# --------------------------------------------------------------------------- #
# Portfolio is a subset of Net Worth — exact aggregation                       #
# --------------------------------------------------------------------------- #
async def test_portfolio_is_subset_of_networth(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _seed_portfolio(db_session, user)
    await _gxbank(db_session, user, Decimal("5000"))

    summary = await networth.summary(
        db_session, user, prices=_PRICES, fx_rate=_FX
    )
    # Investment leg = portfolio NAV USD × FX = 1,000 × 4.45 = RM4,450.0000.
    assert summary.portfolio.nav_usd == Decimal("1000.0000")
    assert summary.investment_myr == Decimal("4450.0000")
    assert summary.portfolio.holdings_count == 2
    assert summary.portfolio.priced is True
    # Cash leg = operational GXBank balance = RM5,000.0000 (counted once).
    assert summary.cash_myr == Decimal("5000.0000")
    # Total = investment + cash + 0 other − 0 liabilities = RM9,450.0000.
    assert summary.total_net_worth_myr == Decimal("9450.0000")
    # The INVESTMENT breakdown line equals the portfolio subset (live source).
    breakdown = _breakdown(summary)
    assert breakdown["INVESTMENT"] == Decimal("4450.0000")
    investment_item = next(
        item for item in summary.breakdown if item.category == "INVESTMENT"
    )
    assert investment_item.source == "live"


# --------------------------------------------------------------------------- #
# Liabilities subtract                                                          #
# --------------------------------------------------------------------------- #
async def test_liabilities_subtract(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _gxbank(db_session, user, Decimal("10000"))
    # A manual OTHER_ASSET (+RM2,000) and a LIABILITY (RM3,000, subtracted).
    db_session.add_all(
        [
            NetWorthEntry(
                user_id=user.id,
                entry_date=date(2026, 1, 10),
                category=NetWorthCategory.OTHER_ASSET.value,
                label="Car",
                amount_myr=Decimal("2000.0000"),
            ),
            NetWorthEntry(
                user_id=user.id,
                entry_date=date(2026, 1, 10),
                category=NetWorthCategory.LIABILITY.value,
                label="Credit Card",
                amount_myr=Decimal("3000.0000"),
                is_liability=True,
            ),
        ]
    )
    await db_session.commit()
    summary = await networth.summary(db_session, user, fx_rate=_FX)
    breakdown = _breakdown(summary)
    # Liability is stored positive in the breakdown line but subtracted.
    assert breakdown["LIABILITY"] == Decimal("3000.0000")
    assert breakdown["OTHER_ASSET"] == Decimal("2000.0000")
    assert summary.liabilities_myr == Decimal("3000.0000")
    assert summary.other_assets_myr == Decimal("2000.0000")
    # Total = 0 investment + 10,000 cash + 2,000 other − 3,000 liab = RM9,000.
    assert summary.total_net_worth_myr == Decimal("9000.0000")


# --------------------------------------------------------------------------- #
# Operational cash counted once; manual CASH entry de-duped (no double-count)   #
# --------------------------------------------------------------------------- #
async def test_manual_cash_deduped_against_operational(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _gxbank(db_session, user, Decimal("5000"))
    # A manual CASH entry while operational CASH exists is treated as a duplicate
    # of operationally-modelled cash and excluded (the §19.4 de-dup rule).
    db_session.add(
        NetWorthEntry(
            user_id=user.id,
            entry_date=date(2026, 1, 10),
            category=NetWorthCategory.CASH.value,
            label="Wallet",
            amount_myr=Decimal("9999.0000"),
        )
    )
    await db_session.commit()
    summary = await networth.summary(db_session, user, fx_rate=_FX)
    # Cash is exactly the operational balance — the manual CASH entry did NOT
    # add a second RM9,999 (no double-count).
    assert summary.cash_myr == Decimal("5000.0000")
    assert summary.total_net_worth_myr == Decimal("5000.0000")


async def test_manual_cash_additive_without_operational(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # No operational cash accounts at all -> a manual CASH entry IS additive
    # (it is the only cash the user models). RM1,200 wallet cash.
    db_session.add(
        NetWorthEntry(
            user_id=user.id,
            entry_date=date(2026, 1, 10),
            category=NetWorthCategory.CASH.value,
            label="Wallet",
            amount_myr=Decimal("1200.0000"),
        )
    )
    await db_session.commit()
    summary = await networth.summary(db_session, user, fx_rate=_FX)
    assert summary.cash_myr == Decimal("1200.0000")
    assert summary.total_net_worth_myr == Decimal("1200.0000")


# --------------------------------------------------------------------------- #
# Emergency fund excluded from deployable but counted in net worth             #
# --------------------------------------------------------------------------- #
async def test_emergency_fund_in_networth_not_deployable(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # GXBank (buffer source) RM5,000 + emergency fund (non-buffer) RM10,000.
    await _gxbank(db_session, user, Decimal("5000"))
    emergency = await cash.create_account(
        db_session,
        user,
        name="Emergency Fund",
        account_type=CashAccountType.EMERGENCY_FUND,
        is_buffer_source=False,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=emergency.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("10000"),
        movement_date=date(2026, 1, 5),
    )
    summary = await networth.summary(db_session, user, fx_rate=_FX)
    breakdown = _breakdown(summary)
    # The emergency fund sits in its own EMERGENCY_FUND bucket (not CASH).
    assert breakdown["CASH"] == Decimal("5000.0000")
    assert breakdown["EMERGENCY_FUND"] == Decimal("10000.0000")
    # It is part of net worth (15,000 total) but excluded from deployable.
    assert summary.total_net_worth_myr == Decimal("15000.0000")
    assert summary.deployable_surplus_myr == Decimal("5000.0000")


# --------------------------------------------------------------------------- #
# Investment leg degrades gracefully when unpriced (never crashes)             #
# --------------------------------------------------------------------------- #
async def test_unpriced_investment_reported_zero_flagged(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _seed_portfolio(db_session, user)
    await _gxbank(db_session, user, Decimal("5000"))
    # No prices supplied for held symbols -> investment leg reported as zero with
    # priced=False; cash still aggregates (the report never crashes).
    summary = await networth.summary(db_session, user, fx_rate=_FX)
    assert summary.investment_myr == Decimal("0.0000")
    assert summary.portfolio.priced is False
    assert summary.portfolio.holdings_count == 2
    assert summary.cash_myr == Decimal("5000.0000")
    assert summary.total_net_worth_myr == Decimal("5000.0000")


# --------------------------------------------------------------------------- #
# Snapshot capture / upsert (reporting layer, §19.4, Decision Log 23)          #
# --------------------------------------------------------------------------- #
async def test_snapshot_capture_and_upsert(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account_id = await _gxbank(db_session, user, Decimal("5000"))
    when = date(2026, 1, 31)

    snapshot = await networth.capture_snapshot(db_session, user, when)
    # The snapshot captures operational cash (GXBank RM5,000) at the date.
    assert snapshot.total_cash_myr == Decimal("5000.0000")
    assert snapshot.snapshot_date == when

    # Recording another inflow then re-capturing the SAME date upserts in place
    # (no second row; the figure refreshes to the new balance).
    await cash.create_movement(
        db_session,
        user,
        account_id=account_id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1500"),
        movement_date=date(2026, 1, 20),
    )
    again = await networth.capture_snapshot(db_session, user, when)
    assert again.id == snapshot.id  # upsert, not insert
    assert again.total_cash_myr == Decimal("6500.0000")

    rows = (
        await db_session.execute(
            select(NetWorthCashSnapshot).where(
                NetWorthCashSnapshot.user_id == user.id,
                NetWorthCashSnapshot.snapshot_date == when,
            )
        )
    ).scalars().all()
    assert len(rows) == 1  # exactly one snapshot for (user, date)


# --------------------------------------------------------------------------- #
# History reconstruction + month-over-month change from snapshots             #
# --------------------------------------------------------------------------- #
async def test_history_points_and_month_change(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account_id = await _gxbank(db_session, user, Decimal("4000"))
    # Capture a Jan 15 snapshot (cash RM4,000), then add RM1,000 and capture a
    # Feb 28 snapshot (cash RM5,000). The month-change baseline is the latest
    # point at or before one calendar month before the latest point: cutoff =
    # Feb 28 − 1 month = Jan 28, so the Jan 15 snapshot qualifies.
    await networth.capture_snapshot(db_session, user, date(2026, 1, 15))
    await cash.create_movement(
        db_session,
        user,
        account_id=account_id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 2, 10),
    )
    await networth.capture_snapshot(db_session, user, date(2026, 2, 28))

    result = await networth.history(db_session, user, fx_rate=_FX)
    # Two history points, oldest-first; each total is pure cash here.
    assert [p.point_date for p in result.points] == [
        date(2026, 1, 15),
        date(2026, 2, 28),
    ]
    assert result.points[0].cash_myr == Decimal("4000.0000")
    assert result.points[0].total_net_worth_myr == Decimal("4000.0000")
    assert result.points[1].cash_myr == Decimal("5000.0000")
    assert result.points[1].total_net_worth_myr == Decimal("5000.0000")
    # Month change of the latest (Feb, RM5,000) vs the Jan baseline (RM4,000):
    # abs +RM1,000.0000; pct = 1,000 / 4,000 × 100 = 25.0000%.
    assert result.change_1m is not None
    assert result.change_1m.abs_myr == Decimal("1000.0000")
    assert result.change_1m.pct == Decimal("25.0000")


async def test_summary_change_1m_from_history(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _gxbank(db_session, user, Decimal("4000"))
    # A baseline snapshot one month before the as-of reference (Mar 15).
    await networth.capture_snapshot(db_session, user, date(2026, 1, 31))
    # Summary as-of Mar 15: live cash is still RM4,000, and the Jan baseline
    # (RM4,000) gives a 0 change_1m (no movement since).
    summary = await networth.summary(
        db_session, user, fx_rate=_FX, as_of=date(2026, 3, 15)
    )
    assert summary.cash_myr == Decimal("4000.0000")
    assert summary.change_1m is not None
    assert summary.change_1m.abs_myr == Decimal("0.0000")

"""Net Worth reporting aggregate — Portfolio is a subset of Net Worth.

This is the **reporting layer** (DESIGN §19.4, Decision Log 18 & 23). It
*references* the operational cash system (``cash_accounts`` / ``cash_movements``,
§19.1) and the portfolio ledger (Phase-1 ``ledger`` -> ``valuation``); it never
mutates either. The canonical aggregation, all in MYR via the latest FX::

    net_worth_myr =  investment_myr        (portfolio NAV USD x FX, live valuation §7.1)
                   + cash_myr              (reporting view of operational cash §19.1, by account_type)
                   + other_assets_myr      (net_worth_entries, non-cash asset categories)
                   - liabilities_myr        (net_worth_entries, LIABILITY category)

Cash separation (Decision Log 23): the operational truth for cash lives in
``cash_accounts`` / ``cash_movements``. Net Worth reads a cash figure for
reporting (live operational balances for "now"; ``net_worth_cash_snapshots`` for
history) and snapshots it so historical net worth is reconstructable
independently of later cash movements. ``net_worth_entries.CASH`` remains
available for purely manual/external cash the user does not model as an
operational account — it is **additive, not a replacement**, and de-duplicated
against operational cash so the same ringgit is never counted twice.

Units: all aggregate amounts are MYR :class:`decimal.Decimal` (4dp via
:class:`app.db.types.Money`); ``weight_pct`` fields are percentages on the
0–100 scale; ``nav_usd`` is USD (4dp). Every query is filtered by ``user_id``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash import CashAccountType
from app.models.net_worth import NetWorthCategory, NetWorthEntry
from app.models.networth_snapshot import (
    NetWorthCashSnapshot,
    NetWorthCashSnapshotSource,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.services import cash
from app.services.ledger import replay
from app.services.valuation import Valuation, valuation
from app.utils.dates import add_months, kl_today
from app.utils.money import Q2, Q4, ZERO, safe_div

_HUNDRED: Final[Decimal] = Decimal("100")

# Breakdown source labels (§19.4): live = derived from the ledger / operational
# cash system; manual = entered via net_worth_entries.
SourceKind = Literal["live", "manual"]

# The fixed Net Worth breakdown categories (DESIGN §19.4). INVESTMENT is the
# live portfolio subset; CASH/EMERGENCY_FUND/BUSINESS draw from operational cash
# (and de-duplicated manual entries); OTHER_ASSET/LIABILITY are manual.
BreakdownCategory = Literal[
    "INVESTMENT",
    "CASH",
    "EMERGENCY_FUND",
    "BUSINESS",
    "OTHER_ASSET",
    "LIABILITY",
]

_BREAKDOWN_ORDER: Final[tuple[BreakdownCategory, ...]] = (
    "INVESTMENT",
    "CASH",
    "EMERGENCY_FUND",
    "BUSINESS",
    "OTHER_ASSET",
    "LIABILITY",
)

# Map an operational cash-account type to its Net Worth breakdown category.
# GXBank/savings/broker-MYR/other are spendable cash; the emergency fund and
# business accounts keep their own buckets (§19.4).
_ACCOUNT_TYPE_TO_CATEGORY: Final[dict[str, BreakdownCategory]] = {
    CashAccountType.GXBANK.value: "CASH",
    CashAccountType.SAVINGS.value: "CASH",
    CashAccountType.BROKER_CASH_MYR.value: "CASH",
    CashAccountType.OTHER.value: "CASH",
    CashAccountType.EMERGENCY_FUND.value: "EMERGENCY_FUND",
    CashAccountType.BUSINESS.value: "BUSINESS",
}

# Map a manual net_worth_entries category to its Net Worth breakdown category.
# SAVINGS is cash-like, so it joins the CASH bucket (mirroring the operational
# SAVINGS account mapping above) for a consistent six-category breakdown.
_ENTRY_CATEGORY_TO_CATEGORY: Final[dict[str, BreakdownCategory]] = {
    NetWorthCategory.CASH.value: "CASH",
    NetWorthCategory.SAVINGS.value: "CASH",
    NetWorthCategory.EMERGENCY_FUND.value: "EMERGENCY_FUND",
    NetWorthCategory.BUSINESS.value: "BUSINESS",
    NetWorthCategory.OTHER_ASSET.value: "OTHER_ASSET",
    NetWorthCategory.LIABILITY.value: "LIABILITY",
}

# Manual-entry categories whose ringgit are already represented by the
# operational cash system. When operational accounts exist for one of these
# buckets, a manual entry mapping to the same bucket is treated as a duplicate
# of operationally-modelled cash and excluded (the de-dup rule of §19.4).
_OPERATIONAL_CASH_CATEGORIES: Final[frozenset[BreakdownCategory]] = frozenset(
    {"CASH", "EMERGENCY_FUND", "BUSINESS"}
)


# --------------------------------------------------------------------------- #
# Result shapes (the API schema mirrors these exactly)                         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PortfolioSubset:
    """The investment subset of Net Worth (DESIGN §19.4).

    ``nav_usd`` is USD (4dp); ``nav_myr`` is MYR (2dp display). ``priced`` is
    ``False`` when no usable prices/FX were available and the investment leg was
    reported as zero (never crashes — see :func:`summary`).
    """

    nav_usd: Decimal
    nav_myr: Decimal
    holdings_count: int
    fx_rate: Decimal | None
    priced: bool


@dataclass(frozen=True)
class BreakdownItem:
    """One Net Worth category line: MYR amount, weight (0–100) and source.

    ``amount_myr`` is signed in the aggregate sense — every line is stored
    positive here; LIABILITY is subtracted in the total (it is reported with a
    positive ``amount_myr`` and ``source='manual'``). ``weight_pct`` is the
    category's share of gross assets (positive categories only) on the 0–100
    scale, ``None`` when there are no gross assets.
    """

    category: BreakdownCategory
    amount_myr: Decimal
    weight_pct: Decimal | None
    source: SourceKind


@dataclass(frozen=True)
class NetWorthChange:
    """Absolute (MYR) and percentage change of total net worth vs a baseline."""

    abs_myr: Decimal
    pct: Decimal | None


@dataclass(frozen=True)
class NetWorthSummary:
    """Full Net Worth reporting aggregate (the API response mirrors this).

    ``total_net_worth_myr`` = investment + cash + other assets − liabilities.
    ``breakdown`` is ordered ``INVESTMENT, CASH, EMERGENCY_FUND, BUSINESS,
    OTHER_ASSET, LIABILITY``. ``deployable_surplus_myr`` comes from the
    operational cash service (never recomputed here). ``change_1m`` / ``change_1y``
    are ``None`` until history exists.
    """

    as_of: date
    total_net_worth_myr: Decimal
    investment_myr: Decimal
    cash_myr: Decimal
    other_assets_myr: Decimal
    liabilities_myr: Decimal
    breakdown: list[BreakdownItem]
    portfolio: PortfolioSubset
    deployable_surplus_myr: Decimal
    change_1m: NetWorthChange | None
    change_1y: NetWorthChange | None


@dataclass(frozen=True)
class NetWorthHistoryPoint:
    """One reconstructed historical net-worth point (MYR).

    ``cash_myr`` is read from the matching ``net_worth_cash_snapshots`` row
    (operational truth captured at the time); ``investment_myr`` is the
    portfolio NAV as of the point date when derivable from the supplied price
    history, else ``None``. ``total_net_worth_myr`` is ``None`` when the
    investment leg could not be derived for that date.
    """

    point_date: date
    cash_myr: Decimal
    investment_myr: Decimal | None
    other_assets_myr: Decimal
    liabilities_myr: Decimal
    total_net_worth_myr: Decimal | None


@dataclass(frozen=True)
class NetWorthHistory:
    """A time series of historical net-worth points plus change helpers.

    ``change_1m`` / ``change_1y`` compare the latest point against the closest
    point at/just before one calendar month / one year earlier; ``None`` until
    enough history exists.
    """

    points: list[NetWorthHistoryPoint]
    change_1m: NetWorthChange | None
    change_1y: NetWorthChange | None


# --------------------------------------------------------------------------- #
# Loaders (per-user isolation)                                                 #
# --------------------------------------------------------------------------- #
async def _load_transactions(
    db: AsyncSession, user_id: int
) -> list[Transaction]:
    """Load the user's full transaction ledger in (date, id) order."""
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    return list(result.scalars().all())


async def _latest_net_worth_entries(
    db: AsyncSession, user_id: int, as_of: date | None
) -> list[NetWorthEntry]:
    """Return the latest entry per ``(category, label)`` on or before ``as_of``.

    Net-worth entries are point-in-time captures; the current value of a manual
    asset/liability is its most recent entry. Grouping by ``(category, label)``
    lets a user track several distinct assets within one category.
    """
    criteria = [NetWorthEntry.user_id == user_id]
    if as_of is not None:
        criteria.append(NetWorthEntry.entry_date <= as_of)
    result = await db.execute(
        select(NetWorthEntry)
        .where(*criteria)
        .order_by(NetWorthEntry.entry_date, NetWorthEntry.id)
    )
    latest: dict[tuple[str, str], NetWorthEntry] = {}
    for entry in result.scalars().all():
        # Later rows overwrite earlier ones for the same (category, label),
        # leaving the most recent value per tracked asset.
        latest[(entry.category, entry.label)] = entry
    return list(latest.values())


# --------------------------------------------------------------------------- #
# Investment leg (portfolio subset — graceful when unpriced)                   #
# --------------------------------------------------------------------------- #
def _value_portfolio(
    transactions: Sequence[Transaction],
    prices: dict[str, Decimal] | None,
    fx_rate: Decimal | None,
    as_of: date | None,
) -> tuple[Valuation | None, PortfolioSubset]:
    """Price the replayed ledger, falling back to a zero investment leg.

    Returns ``(valuation_or_None, PortfolioSubset)``. The investment leg is
    reported as zero with ``priced=False`` (never crashes) when there are no
    held positions, when ``prices``/``fx_rate`` are missing, or when pricing
    fails for any reason (e.g. a held symbol has no supplied price) — following
    DESIGN §19.4's "never crash; report investment as 0 with a flag" rule.
    """
    state = replay(transactions, as_of=as_of)
    held = [
        position
        for position in state.positions.values()
        if position.quantity > ZERO
    ]
    holdings_count = len(held)

    if fx_rate is None or fx_rate <= ZERO:
        # No FX -> we cannot express NAV in MYR; report a zero investment leg.
        return None, PortfolioSubset(
            nav_usd=ZERO,
            nav_myr=Q2(ZERO),
            holdings_count=holdings_count,
            fx_rate=None,
            priced=False,
        )

    if not held:
        # Pure-cash (or empty) portfolio: NAV is just the broker cash balance,
        # which is fully priced (no per-symbol price needed).
        snapshot = valuation(state, {}, fx_rate)
        return snapshot, PortfolioSubset(
            nav_usd=snapshot.nav_usd,
            nav_myr=snapshot.nav_myr,
            holdings_count=0,
            fx_rate=fx_rate,
            priced=True,
        )

    if prices is None:
        return None, PortfolioSubset(
            nav_usd=ZERO,
            nav_myr=Q2(ZERO),
            holdings_count=holdings_count,
            fx_rate=fx_rate,
            priced=False,
        )

    try:
        snapshot = valuation(state, prices, fx_rate)
    except Exception:
        # Missing/invalid price for a held symbol, or any other valuation
        # failure: degrade gracefully to a zero, flagged investment leg rather
        # than failing the whole net-worth report (§19.4).
        return None, PortfolioSubset(
            nav_usd=ZERO,
            nav_myr=Q2(ZERO),
            holdings_count=holdings_count,
            fx_rate=fx_rate,
            priced=False,
        )
    return snapshot, PortfolioSubset(
        nav_usd=snapshot.nav_usd,
        nav_myr=snapshot.nav_myr,
        holdings_count=holdings_count,
        fx_rate=fx_rate,
        priced=True,
    )


# --------------------------------------------------------------------------- #
# Cash leg (reporting view of operational balances — never mutates cash)       #
# --------------------------------------------------------------------------- #
async def _operational_cash_by_category(
    db: AsyncSession, user: User, as_of: date | None
) -> dict[BreakdownCategory, Decimal]:
    """Reporting view of operational cash, summed by breakdown category.

    Reads (never mutates) the operational cash system: derives each non-archived
    account's MYR balance from its movement ledger and groups by
    :data:`_ACCOUNT_TYPE_TO_CATEGORY`. Returns MYR Decimals (4dp).
    """
    accounts = await cash.list_accounts(db, user)
    account_balances = await cash.balances(db, user, as_of)
    by_category: dict[BreakdownCategory, Decimal] = {}
    for account in accounts:
        category = _ACCOUNT_TYPE_TO_CATEGORY.get(account.account_type, "CASH")
        amount = account_balances.get(account.id, ZERO)
        by_category[category] = Q4(by_category.get(category, ZERO) + amount)
    return by_category


def _manual_entries_by_category(
    entries: Sequence[NetWorthEntry],
    operational_categories: frozenset[BreakdownCategory],
) -> dict[BreakdownCategory, Decimal]:
    """Sum manual net-worth entries by breakdown category, de-duped vs cash.

    A manual entry that maps to an operational-cash category (CASH /
    EMERGENCY_FUND / BUSINESS) is **excluded** when the user already models that
    category operationally (``operational_categories``), so the same ringgit is
    never double-counted (§19.4 de-dup rule). LIABILITY amounts are summed
    positive here; the caller subtracts them. Returns MYR Decimals (4dp).
    """
    by_category: dict[BreakdownCategory, Decimal] = {}
    for entry in entries:
        category = _ENTRY_CATEGORY_TO_CATEGORY.get(
            entry.category, "OTHER_ASSET"
        )
        if (
            category in _OPERATIONAL_CASH_CATEGORIES
            and category in operational_categories
        ):
            # Operational cash is the source of truth for this category; the
            # manual entry would duplicate operationally-modelled cash.
            continue
        amount = Q4(entry.amount_myr)
        by_category[category] = Q4(by_category.get(category, ZERO) + amount)
    return by_category


# --------------------------------------------------------------------------- #
# Summary (the primary entrypoint)                                             #
# --------------------------------------------------------------------------- #
async def summary(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
    as_of: date | None = None,
) -> NetWorthSummary:
    """Build the Net Worth reporting aggregate for ``user`` (DESIGN §19.4).

    ``prices`` (USD per held symbol) and ``fx_rate`` (USD->MYR) price the live
    investment leg; when missing or unusable the investment leg is reported as
    zero with ``portfolio.priced = False`` (never crashes). ``cash_myr`` is a
    read-only reporting view of operational cash balances (the cash system is
    never mutated). ``other_assets_myr`` and ``liabilities_myr`` come from the
    latest ``net_worth_entries``, with manual CASH/EMERGENCY_FUND/BUSINESS
    de-duplicated against operational cash. ``deployable_surplus_myr`` is read
    from the operational cash service. ``change_1m`` / ``change_1y`` are filled
    from ``net_worth_cash_snapshots`` history when available.
    """
    reference_date = as_of if as_of is not None else kl_today()

    # --- Investment leg (portfolio subset) ---
    transactions = await _load_transactions(db, user.id)
    snapshot, portfolio = _value_portfolio(
        transactions, prices, fx_rate, as_of
    )
    investment_myr = (
        Q4(snapshot.nav_usd * snapshot.fx_rate)
        if snapshot is not None
        else ZERO
    )

    # --- Cash leg (reporting view of operational balances) ---
    operational_cash = await _operational_cash_by_category(
        db, user, as_of
    )
    operational_categories = frozenset(operational_cash)

    # --- Manual assets / liabilities (de-duped against operational cash) ---
    entries = await _latest_net_worth_entries(db, user.id, as_of)
    manual = _manual_entries_by_category(entries, operational_categories)

    # --- Aggregate per category ---
    # Combine operational cash and (de-duped) manual entries into the six fixed
    # breakdown buckets. INVESTMENT is always the live portfolio subset.
    amounts: dict[BreakdownCategory, Decimal] = {
        category: ZERO for category in _BREAKDOWN_ORDER
    }
    sources: dict[BreakdownCategory, SourceKind] = {
        "INVESTMENT": "live",
        "CASH": "manual",
        "EMERGENCY_FUND": "manual",
        "BUSINESS": "manual",
        "OTHER_ASSET": "manual",
        "LIABILITY": "manual",
    }
    amounts["INVESTMENT"] = investment_myr
    for category, amount in operational_cash.items():
        amounts[category] = Q4(amounts[category] + amount)
        # Any operational cash in a category makes it (at least partly) live.
        sources[category] = "live"
    for category, amount in manual.items():
        amounts[category] = Q4(amounts[category] + amount)

    cash_myr = amounts["CASH"]
    other_assets_myr = Q4(
        amounts["EMERGENCY_FUND"]
        + amounts["BUSINESS"]
        + amounts["OTHER_ASSET"]
    )
    liabilities_myr = amounts["LIABILITY"]
    total_net_worth_myr = Q4(
        investment_myr + cash_myr + other_assets_myr - liabilities_myr
    )

    # --- Weights: each positive category's share of gross assets (0–100) ---
    gross_assets = Q4(
        sum(
            (
                amounts[category]
                for category in _BREAKDOWN_ORDER
                if category != "LIABILITY"
            ),
            start=ZERO,
        )
    )
    breakdown: list[BreakdownItem] = []
    for category in _BREAKDOWN_ORDER:
        amount = amounts[category]
        # Weight is each category's share of gross (non-liability) assets, on
        # the 0–100 scale; the LIABILITY line reports its size relative to the
        # same gross-asset base. None when there are no gross assets.
        weight_pct: Decimal | None = (
            Q4(amount * _HUNDRED / gross_assets)
            if gross_assets > ZERO
            else None
        )
        breakdown.append(
            BreakdownItem(
                category=category,
                amount_myr=amount,
                weight_pct=weight_pct,
                source=sources[category],
            )
        )

    # --- Deployable surplus (operational cash service; never recomputed) ---
    deployable = await cash.deployable_surplus_myr(db, user, as_of)

    # --- Month / year change from snapshot-backed history ---
    history_result = await history(db, user, prices=prices, fx_rate=fx_rate)
    change_1m, change_1y = _changes_from(
        total_net_worth_myr, reference_date, history_result.points
    )

    return NetWorthSummary(
        as_of=reference_date,
        total_net_worth_myr=total_net_worth_myr,
        investment_myr=investment_myr,
        cash_myr=cash_myr,
        other_assets_myr=other_assets_myr,
        liabilities_myr=liabilities_myr,
        breakdown=breakdown,
        portfolio=portfolio,
        deployable_surplus_myr=deployable,
        change_1m=change_1m,
        change_1y=change_1y,
    )


# --------------------------------------------------------------------------- #
# Snapshot capture (reporting layer — upsert by (user, date))                  #
# --------------------------------------------------------------------------- #
async def capture_snapshot(
    db: AsyncSession, user: User, snapshot_date: date | None = None
) -> NetWorthCashSnapshot:
    """Capture the operational cash position into ``net_worth_cash_snapshots``.

    Reads (never mutates) operational balances as of ``snapshot_date`` (default
    today, KL), totals them and records a per-account-type breakdown. Upserts by
    ``(user, snapshot_date)`` — re-capturing the same date refreshes the figures
    in place. ``source='auto'``. Commits and returns the persisted row. This is
    the reporting capture that makes historical net worth reconstructable
    independently of later cash movements (§19.4, Decision Log 23).
    """
    when = snapshot_date if snapshot_date is not None else kl_today()

    accounts = await cash.list_accounts(db, user)
    account_balances = await cash.balances(db, user, when)
    breakdown: dict[str, Decimal] = {}
    for account in accounts:
        breakdown[account.account_type] = Q4(
            breakdown.get(account.account_type, ZERO)
            + account_balances.get(account.id, ZERO)
        )
    total_cash_myr = Q4(sum(breakdown.values(), start=ZERO))
    breakdown_json = json.dumps(
        {account_type: str(amount) for account_type, amount in breakdown.items()}
    )

    existing_result = await db.execute(
        select(NetWorthCashSnapshot).where(
            NetWorthCashSnapshot.user_id == user.id,
            NetWorthCashSnapshot.snapshot_date == when,
        )
    )
    snapshot = existing_result.scalar_one_or_none()
    if snapshot is None:
        snapshot = NetWorthCashSnapshot(
            user_id=user.id,
            snapshot_date=when,
            total_cash_myr=total_cash_myr,
            breakdown=breakdown_json,
            source=NetWorthCashSnapshotSource.AUTO.value,
        )
        db.add(snapshot)
    else:
        snapshot.total_cash_myr = total_cash_myr
        snapshot.breakdown = breakdown_json
        snapshot.source = NetWorthCashSnapshotSource.AUTO.value
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


# --------------------------------------------------------------------------- #
# History (snapshot-backed cash + derivable month-end portfolio values)        #
# --------------------------------------------------------------------------- #
async def history(
    db: AsyncSession,
    user: User,
    range: str | None = None,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
) -> NetWorthHistory:
    """Reconstruct historical net worth from cash snapshots + manual entries.

    Each ``net_worth_cash_snapshots`` row supplies the operational cash figure
    captured at that date (operational vs reporting separation stays explicit:
    history reads the *reporting* snapshot, not live operational balances). For
    each snapshot date the investment leg is the portfolio NAV as of that date
    when derivable from the supplied ``prices``/``fx_rate`` (else ``None``, and
    the point's total is ``None``); manual assets/liabilities use the latest
    entry on or before the date, de-duped against the snapshot's account-type
    breakdown. ``range`` (``3m`` | ``6m`` | ``1y`` | ``all``) limits the window
    from today (KL); ``None`` / unknown means all history.

    Returns points ordered oldest-first plus ``change_1m`` / ``change_1y``.
    """
    snapshots_result = await db.execute(
        select(NetWorthCashSnapshot)
        .where(NetWorthCashSnapshot.user_id == user.id)
        .order_by(NetWorthCashSnapshot.snapshot_date)
    )
    snapshots = list(snapshots_result.scalars().all())

    start = _range_start(range)
    if start is not None:
        snapshots = [
            snap for snap in snapshots if snap.snapshot_date >= start
        ]

    transactions = await _load_transactions(db, user.id)

    points: list[NetWorthHistoryPoint] = []
    for snap in snapshots:
        point_date = snap.snapshot_date
        cash_myr = Q4(snap.total_cash_myr)

        # Manual assets/liabilities as of the snapshot date, de-duped against
        # the operational cash buckets that the snapshot already captured.
        entries = await _latest_net_worth_entries(db, user.id, point_date)
        operational_categories = _categories_in_snapshot(snap)
        manual = _manual_entries_by_category(entries, operational_categories)
        other_assets_myr = Q4(
            manual.get("EMERGENCY_FUND", ZERO)
            + manual.get("BUSINESS", ZERO)
            + manual.get("OTHER_ASSET", ZERO)
        )
        liabilities_myr = manual.get("LIABILITY", ZERO)
        # Manual CASH not modelled operationally is additive to reported cash.
        cash_myr = Q4(cash_myr + manual.get("CASH", ZERO))

        snapshot_val, _ = _value_portfolio(
            transactions, prices, fx_rate, point_date
        )
        investment_myr: Decimal | None = (
            Q4(snapshot_val.nav_usd * snapshot_val.fx_rate)
            if snapshot_val is not None
            else None
        )
        total: Decimal | None = (
            Q4(
                investment_myr
                + cash_myr
                + other_assets_myr
                - liabilities_myr
            )
            if investment_myr is not None
            else None
        )
        points.append(
            NetWorthHistoryPoint(
                point_date=point_date,
                cash_myr=cash_myr,
                investment_myr=investment_myr,
                other_assets_myr=other_assets_myr,
                liabilities_myr=liabilities_myr,
                total_net_worth_myr=total,
            )
        )

    latest_total = (
        points[-1].total_net_worth_myr if points else None
    )
    latest_date = points[-1].point_date if points else kl_today()
    if latest_total is not None:
        change_1m, change_1y = _changes_from(
            latest_total, latest_date, points[:-1]
        )
    else:
        change_1m = change_1y = None

    return NetWorthHistory(
        points=points, change_1m=change_1m, change_1y=change_1y
    )


# --------------------------------------------------------------------------- #
# Change / range helpers                                                       #
# --------------------------------------------------------------------------- #
def _change(current: Decimal, baseline: Decimal) -> NetWorthChange:
    """Build an absolute + percentage change from ``baseline`` to ``current``.

    ``pct`` is ``(current − baseline) / baseline × 100`` on the 0–100 scale,
    ``None`` when the baseline is zero (division guarded)."""
    abs_myr = Q4(current - baseline)
    pct = (
        Q4(safe_div(abs_myr, baseline) * _HUNDRED) if baseline != ZERO else None
    )
    return NetWorthChange(abs_myr=abs_myr, pct=pct)


def _baseline_at_or_before(
    cutoff: date, points: Sequence[NetWorthHistoryPoint]
) -> Decimal | None:
    """Latest historical total at or before ``cutoff`` (``None`` if none).

    Points whose total could not be derived (``None``) are skipped — a baseline
    must be a fully-valued net-worth figure for the change to be meaningful.
    """
    chosen: Decimal | None = None
    for point in points:
        if (
            point.point_date <= cutoff
            and point.total_net_worth_myr is not None
        ):
            chosen = point.total_net_worth_myr
    return chosen


def _changes_from(
    current_total: Decimal,
    reference_date: date,
    history_points: Sequence[NetWorthHistoryPoint],
) -> tuple[NetWorthChange | None, NetWorthChange | None]:
    """Month/year change of ``current_total`` vs the closest prior history point.

    Compares against the latest derivable point at or before one calendar month
    / one year before ``reference_date``. Returns ``(change_1m, change_1y)``,
    each ``None`` when no such baseline exists.
    """
    if not history_points:
        return None, None
    month_cutoff = add_months(reference_date, -1)
    year_cutoff = add_months(reference_date, -12)
    month_baseline = _baseline_at_or_before(month_cutoff, history_points)
    year_baseline = _baseline_at_or_before(year_cutoff, history_points)
    change_1m = (
        _change(current_total, month_baseline)
        if month_baseline is not None
        else None
    )
    change_1y = (
        _change(current_total, year_baseline)
        if year_baseline is not None
        else None
    )
    return change_1m, change_1y


def _range_start(range: str | None) -> date | None:
    """Translate a range token (``3m`` | ``6m`` | ``1y`` | ``all``) into a start
    date relative to today (KL). ``None`` / ``all`` / unknown means no limit."""
    if range is None:
        return None
    token = range.strip().lower()
    today = kl_today()
    if token == "3m":
        return add_months(today, -3)
    if token == "6m":
        return add_months(today, -6)
    if token == "1y":
        return add_months(today, -12)
    return None


def _categories_in_snapshot(
    snapshot: NetWorthCashSnapshot,
) -> frozenset[BreakdownCategory]:
    """Breakdown categories represented by a snapshot's account-type breakdown.

    Used to de-dup manual entries during history reconstruction the same way
    live operational balances de-dup them in :func:`summary`. A malformed
    breakdown JSON degrades to an empty set (no de-dup) rather than raising.
    """
    try:
        parsed = json.loads(snapshot.breakdown)
    except (json.JSONDecodeError, TypeError):
        return frozenset()
    if not isinstance(parsed, dict):
        return frozenset()
    categories: set[BreakdownCategory] = set()
    for account_type in parsed:
        category = _ACCOUNT_TYPE_TO_CATEGORY.get(str(account_type))
        if category is not None:
            categories.add(category)
    return frozenset(categories)

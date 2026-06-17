"""Adversarial probes — networth broker-cash drop + DEPLOY overspend."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.services import cash, execution, networth
from app.services.ledger import replay
from app.services.rebalance import plan_rebalance
from conftest import UserFactory, UserLoader, canonical_transactions

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_probe_broker_cash_dropped_when_held_and_no_prices(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    """1 VOO + broker cash, FX supplied but NO price -> investment leg = 0,
    silently dropping the broker DEPOSIT cash that is part of NAV."""
    seeded = await user_factory()
    user = await load_user(seeded)
    # Persist the canonical ledger (holds 1.5 VOO + 1 QQQ + $1909.20 broker cash)
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        db.add_all(canonical_transactions(user.id))
        await db.commit()

    fx = Decimal("4.45")
    # FX supplied, prices NOT supplied.
    summary = await networth.summary(db_session, user, prices=None, fx_rate=fx)
    print("PRICED:", summary.portfolio.priced)
    print("INVESTMENT_MYR:", summary.investment_myr)
    print("NAV_USD:", summary.portfolio.nav_usd)
    # Broker cash alone (no positions priced) is 1909.20 USD -> ~8495 MYR.
    print("BROKER_CASH_USD_in_ledger:", replay(canonical_transactions(user.id)).cash_usd)
    # The broker cash is silently absent from net worth.
    assert summary.investment_myr == Decimal("0.0000")


async def test_probe_deploy_only_overspends_when_rebalance_needs_sell(
    db_session, user_factory: UserFactory, load_user: UserLoader, ips_factory
) -> None:
    """A DEPLOY-only plan strips SELLs but keeps BUYs floored against
    cash+sell-proceeds, overspending the deployable surplus."""
    from app.models.ips import IpsRule
    seeded = await user_factory()
    user = await load_user(seeded)

    # Build a portfolio that is heavily overweight VOO so a rebalance toward
    # 70/30 requires SELLING VOO to fund a QQQ buy. Small fresh cash surplus.
    # Holdings: 100 VOO @ cost, target 70/30. With tiny cash, full rebalance
    # is SELL_REQUIRED.
    state = replay([])
    # Use pure-engine path to demonstrate the rebalance shape first.
    ips: IpsRule = ips_factory()

    from app.services.ledger import LedgerState, Position
    st = LedgerState()
    st.cash_usd = Decimal("100.0000")  # tiny idle cash
    st.positions["VOO"] = Position(symbol="VOO", quantity=Decimal("100"),
                                   cost_basis_usd=Decimal("40000"))
    # No QQQ held at all -> very underweight QQQ.
    prices = {"VOO": Decimal("500"), "QQQ": Decimal("400")}
    fx = Decimal("4.45")
    deploy_usd = Decimal("100.0000")  # fresh surplus folded in
    plan = plan_rebalance(st, prices, fx, ips, extra_cash_usd=deploy_usd)
    print("REBALANCE_STATUS:", plan.status.value)
    buys = [o for o in plan.orders if o.side == "BUY"]
    sells = [o for o in plan.orders if o.side == "SELL"]
    buy_usd = sum((o.est_amount_usd for o in buys), Decimal("0"))
    sell_usd = sum((o.est_amount_usd for o in sells), Decimal("0"))
    deployable = st.cash_usd + deploy_usd
    print("BUY_USD:", buy_usd, "SELL_USD:", sell_usd, "DEPLOYABLE:", deployable)
    # If we DROP the sells (DEPLOY-only behavior) but keep the buys:
    print("DEPLOY_ONLY_KEPT_BUYS_USD:", buy_usd, "vs cash-only budget:", deployable)
    if plan.status.value == "SELL_REQUIRED":
        assert buy_usd > deployable, (
            "DEPLOY-only would keep buys exceeding the cash budget"
        )

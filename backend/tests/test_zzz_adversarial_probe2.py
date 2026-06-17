"""Adversarial probe 2 — full generate_execution_plan DEPLOY overspend +
networth priced-case correctness."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.models.execution import ExecutionPlanKind
from app.services import cash, execution, networth
from app.services.ledger import replay
from conftest import UserFactory, UserLoader, canonical_transactions

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_full_deploy_plan_overspends(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    from app.db.session import SessionLocal
    from app.models.transaction import Transaction, TransactionType

    seeded = await user_factory()
    user = await load_user(seeded)

    # Build an overweight-VOO portfolio in the DB: deposit then buy 100 VOO.
    async with SessionLocal() as db:
        db.add_all([
            Transaction(
                user_id=user.id, transaction_date=date(2026, 1, 5),
                transaction_type=TransactionType.DEPOSIT.value,
                fee_usd=Decimal("0"), fx_rate_recorded=Decimal("4.45"),
                total_amount_myr=Decimal("223225.00"), notes="",
            ),
            Transaction(
                user_id=user.id, transaction_date=date(2026, 1, 6),
                transaction_type=TransactionType.BUY.value, asset_symbol="VOO",
                quantity=Decimal("100"), unit_price_usd=Decimal("500"),
                fee_usd=Decimal("0"), fx_rate_recorded=Decimal("4.45"),
                total_amount_myr=Decimal("222500.00"), notes="",
            ),
        ])
        await db.commit()

    # Small buffer surplus: RM445 -> $100 deployable.
    acct = await cash.create_account(
        db_session, user, name="GXBank",
        account_type=CashAccountType.GXBANK, is_buffer_source=True,
    )
    await cash.create_movement(
        db_session, user, account_id=acct.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("445"), movement_date=date(2026, 1, 7),
    )

    prices = {"VOO": Decimal("500"), "QQQ": Decimal("400")}
    fx = Decimal("4.45")
    surplus = await cash.deployable_surplus_myr(db_session, user)
    print("DEPLOYABLE_MYR:", surplus, "-> USD:", surplus / fx)

    # Force a DEPLOY-only plan (deploy-only window semantics).
    plan = await execution.generate_execution_plan(
        db_session, user, prices, fx, kind=ExecutionPlanKind.DEPLOY
    )
    print("PLAN_KIND:", plan.plan_kind)
    print("CASH_DEPLOYED_USD:", plan.cash_deployed_usd)
    print("CASH_DEPLOYED_MYR:", plan.cash_deployed_myr)
    import json
    orders = json.loads(plan.orders)
    print("ORDERS:", orders)
    # A DEPLOY-only plan must never deploy more than the deployable surplus.
    deployable_usd = surplus / fx
    assert plan.cash_deployed_usd <= deployable_usd + Decimal("0.01"), (
        f"DEPLOY plan deployed {plan.cash_deployed_usd} USD but only "
        f"{deployable_usd} USD was deployable"
    )


async def test_networth_priced_case_includes_broker_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    """When prices ARE supplied, investment_myr includes positions + broker
    cash correctly."""
    from app.db.session import SessionLocal
    seeded = await user_factory()
    user = await load_user(seeded)
    async with SessionLocal() as db:
        db.add_all(canonical_transactions(user.id))
        await db.commit()

    fx = Decimal("4.45")
    prices = {"VOO": Decimal("500"), "QQQ": Decimal("400")}
    summary = await networth.summary(db_session, user, prices=prices, fx_rate=fx)
    st = replay(canonical_transactions(user.id))
    # Expected NAV USD = 1.5*500 + 1*400 + 1909.20 cash = 750+400+1909.20 = 3059.20
    print("PRICED:", summary.portfolio.priced)
    print("NAV_USD:", summary.portfolio.nav_usd, "expected 3059.2000")
    print("INVESTMENT_MYR:", summary.investment_myr,
          "expected", Decimal("3059.20") * fx)
    assert summary.portfolio.nav_usd == Decimal("3059.2000")

"""Probe 3 — DL23 no double-count between operational cash and entries.CASH."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.models.net_worth import NetWorthCategory, NetWorthEntry
from app.services import cash, networth
from conftest import UserFactory, UserLoader

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_no_double_count_operational_vs_manual_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Operational GXBank cash RM5000.
    acct = await cash.create_account(
        db_session, user, name="GXBank",
        account_type=CashAccountType.GXBANK, is_buffer_source=True,
    )
    await cash.create_movement(
        db_session, user, account_id=acct.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("5000"), movement_date=date(2026, 1, 5),
    )
    # Manual CASH entry RM2000 (should be DE-DUPED away since operational CASH exists).
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        db.add(NetWorthEntry(
            user_id=user.id, entry_date=date(2026, 1, 10),
            category=NetWorthCategory.CASH.value, label="wallet",
            amount_myr=Decimal("2000"), is_liability=False,
        ))
        await db.commit()

    summary = await networth.summary(db_session, user, prices=None, fx_rate=None)
    print("CASH_MYR:", summary.cash_myr)
    # Per the de-dup rule the manual 2000 is dropped; only operational 5000 counts.
    assert summary.cash_myr == Decimal("5000.0000")


async def test_manual_cash_kept_when_no_operational_account(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        db.add(NetWorthEntry(
            user_id=user.id, entry_date=date(2026, 1, 10),
            category=NetWorthCategory.CASH.value, label="wallet",
            amount_myr=Decimal("2000"), is_liability=False,
        ))
        await db.commit()
    summary = await networth.summary(db_session, user, prices=None, fx_rate=None)
    print("CASH_MYR(no-op-acct):", summary.cash_myr)
    assert summary.cash_myr == Decimal("2000.0000")

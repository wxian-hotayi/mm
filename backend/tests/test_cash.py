"""Cash Buffer System (DESIGN §19.1) — derived balances, deployable surplus.

Every balance, total, deployable surplus, buffer-fill ratio and readiness state
is **derived** from the :class:`CashMovement` ledger (never stored). Tests drive
the :mod:`app.services.cash` service through a real :class:`AsyncSession` and
assert exact 4dp Decimals with hand-computed comments. Each test uses its own
isolated user so assertions are order-independent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.services import cash
from conftest import SeededCash, UserFactory, UserLoader

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --------------------------------------------------------------------------- #
# Balance derivation: signs by movement type, transfers, adjustments          #
# --------------------------------------------------------------------------- #
async def test_balance_signs_by_movement_type(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    # +2,000 INFLOW + 15 INTEREST − 500 OUTFLOW = RM1,515.0000.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("2000"),
        movement_date=date(2026, 1, 5),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INTEREST,
        amount_myr=Decimal("15"),
        movement_date=date(2026, 1, 31),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.OUTFLOW,
        amount_myr=Decimal("500"),
        movement_date=date(2026, 2, 1),
    )
    balance = await cash.balance(db_session, user, account.id)
    assert balance == Decimal("1515.0000")


async def test_balance_as_of_excludes_later_movements(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 1, 10),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("400"),
        movement_date=date(2026, 2, 10),
    )
    # As-of Jan 31 only the first inflow counts.
    assert await cash.balance(
        db_session, user, account.id, as_of=date(2026, 1, 31)
    ) == Decimal("1000.0000")
    # As-of Feb 28 both inflows count.
    assert await cash.balance(
        db_session, user, account.id, as_of=date(2026, 2, 28)
    ) == Decimal("1400.0000")


async def test_adjustment_is_signed_by_stored_amount(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 1, 5),
    )
    # A negative ADJUSTMENT subtracts; a positive ADJUSTMENT adds.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.ADJUSTMENT,
        amount_myr=Decimal("-120.5000"),
        movement_date=date(2026, 1, 6),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.ADJUSTMENT,
        amount_myr=Decimal("20.0000"),
        movement_date=date(2026, 1, 7),
    )
    # 1,000 − 120.5 + 20 = RM899.5000.
    assert await cash.balance(db_session, user, account.id) == Decimal(
        "899.5000"
    )


async def test_zero_adjustment_rejected(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    from app.core.errors import ValidationFailed

    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    with pytest.raises(ValidationFailed):
        await cash.create_movement(
            db_session,
            user,
            account_id=account.id,
            movement_type=CashMovementType.ADJUSTMENT,
            amount_myr=Decimal("0"),
            movement_date=date(2026, 1, 7),
        )


async def test_non_adjustment_must_be_positive(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    from app.core.errors import ValidationFailed

    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    with pytest.raises(ValidationFailed):
        await cash.create_movement(
            db_session,
            user,
            account_id=account.id,
            movement_type=CashMovementType.INFLOW,
            amount_myr=Decimal("-1"),
            movement_date=date(2026, 1, 7),
        )


async def test_transfer_moves_balance_between_accounts(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    source = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    destination = await cash.create_account(
        db_session,
        user,
        name="Broker MYR",
        account_type=CashAccountType.BROKER_CASH_MYR,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=source.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("3000"),
        movement_date=date(2026, 1, 5),
    )
    out_leg, in_leg = await cash.transfer(
        db_session,
        user,
        from_account_id=source.id,
        to_account_id=destination.id,
        amount_myr=Decimal("1200"),
        movement_date=date(2026, 1, 10),
    )
    # The TRANSFER_OUT_TO_BROKER leg decreases the source by 1,200; the
    # TRANSFER_IN leg increases the destination by 1,200 (each pointing at the
    # other via counterparty_account_id).
    assert out_leg.movement_type == CashMovementType.TRANSFER_OUT_TO_BROKER.value
    assert in_leg.movement_type == CashMovementType.TRANSFER_IN.value
    assert out_leg.counterparty_account_id == destination.id
    assert in_leg.counterparty_account_id == source.id
    assert await cash.balance(db_session, user, source.id) == Decimal(
        "1800.0000"
    )
    assert await cash.balance(db_session, user, destination.id) == Decimal(
        "1200.0000"
    )
    # Total cash is conserved across a transfer.
    assert await cash.total_cash_myr(db_session, user) == Decimal("3000.0000")


async def test_transfer_to_same_account_rejected(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    from app.core.errors import ValidationFailed

    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    with pytest.raises(ValidationFailed):
        await cash.transfer(
            db_session,
            user,
            from_account_id=account.id,
            to_account_id=account.id,
            amount_myr=Decimal("100"),
            movement_date=date(2026, 1, 10),
        )


# --------------------------------------------------------------------------- #
# Deployable surplus with target buffers                                       #
# --------------------------------------------------------------------------- #
async def test_deployable_surplus_seeded(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    user = await load_user(seeded_cash.user)
    # Buffer source (GXBank) balance is RM5,050; no target buffer reserved.
    assert (
        await cash.balance(db_session, user, seeded_cash.buffer_account_id)
        == seeded_cash.buffer_balance_myr
    )
    # The emergency fund is NOT a buffer source -> excluded from deployable.
    assert (
        await cash.balance(db_session, user, seeded_cash.emergency_id)
        == seeded_cash.emergency_balance_myr
    )
    # Deployable surplus = buffer-source balance − buffer-source target (0).
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "5050.0000"
    )
    # Total cash counts every account (5,050 + 10,000 = RM15,050).
    assert await cash.total_cash_myr(db_session, user) == Decimal(
        "15050.0000"
    )


async def test_target_buffer_reserves_are_never_deployable(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
        target_buffer_myr=Decimal("2000"),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("5000"),
        movement_date=date(2026, 1, 5),
    )
    # Deployable = max(0, 5,000 − 2,000 target) = RM3,000.0000.
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "3000.0000"
    )
    # Buffer fill ratio = balance / target = 5,000 / 2,000 = 2.5000.
    assert await cash.buffer_fill_ratio(db_session, user) == Decimal("2.5000")


async def test_deployable_surplus_floored_at_zero(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
        target_buffer_myr=Decimal("5000"),
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 1, 5),
    )
    # Balance 1,000 < 5,000 target -> deployable max(0, −4,000) = RM0.0000.
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "0.0000"
    )
    # Buffer fill ratio = 1,000 / 5,000 = 0.2000 (clamped at 0 below).
    assert await cash.buffer_fill_ratio(db_session, user) == Decimal("0.2000")


async def test_buffer_fill_ratio_none_without_target(
    db_session, seeded_cash: SeededCash, load_user: UserLoader
) -> None:
    # No target buffer configured anywhere -> the ratio is undefined (None),
    # not a divide-by-zero crash.
    user = await load_user(seeded_cash.user)
    assert await cash.buffer_fill_ratio(db_session, user) is None


# --------------------------------------------------------------------------- #
# Readiness threshold                                                          #
# --------------------------------------------------------------------------- #
async def test_readiness_crosses_threshold(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    # min_deploy_threshold_myr defaults to RM1,500 on the seeded IPS row.
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    # Below threshold -> ACCUMULATING.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1499.9999"),
        movement_date=date(2026, 1, 5),
    )
    assert await cash.readiness(db_session, user) == "ACCUMULATING"
    # Top up to exactly the threshold (>=) -> READY.
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("0.0001"),
        movement_date=date(2026, 1, 6),
    )
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "1500.0000"
    )
    assert await cash.readiness(db_session, user) == "READY"


# --------------------------------------------------------------------------- #
# Account / movement update + delete (mutations re-derive balances)            #
# --------------------------------------------------------------------------- #
async def test_update_account_changes_buffer_math(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session,
        user,
        name="GX",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("4000"),
        movement_date=date(2026, 1, 5),
    )
    # Initially fully deployable (no target).
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "4000.0000"
    )
    # Raising the target buffer and renaming re-derives the surplus from the
    # ledger (balance is never stored).
    updated = await cash.update_account(
        db_session,
        user,
        account.id,
        name="GXBank Main",
        target_buffer_myr=Decimal("1000"),
    )
    assert updated.name == "GXBank Main"
    assert updated.target_buffer_myr == Decimal("1000.0000")
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "3000.0000"
    )
    # Flipping is_buffer_source off removes the account from deployable math.
    await cash.update_account(
        db_session, user, account.id, is_buffer_source=False
    )
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "0.0000"
    )


async def test_update_and_delete_movement_re_derive_balance(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    inflow = await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 1, 5),
    )
    outflow = await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.OUTFLOW,
        amount_myr=Decimal("300"),
        movement_date=date(2026, 1, 6),
    )
    assert await cash.balance(db_session, user, account.id) == Decimal("700.0000")

    # Editing the inflow amount re-derives the balance (1,500 − 300 = 1,200).
    await cash.update_movement(
        db_session, user, inflow.id, amount_myr=Decimal("1500")
    )
    assert await cash.balance(db_session, user, account.id) == Decimal(
        "1200.0000"
    )
    # Reloading the movement reflects the new amount.
    reloaded = await cash.get_movement(db_session, user, inflow.id)
    assert reloaded.amount_myr == Decimal("1500.0000")

    # Deleting the outflow leaves only the inflow (1,500).
    await cash.delete_movement(db_session, user, outflow.id)
    assert await cash.balance(db_session, user, account.id) == Decimal(
        "1500.0000"
    )


async def test_update_movement_rejects_non_positive(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    from app.core.errors import ValidationFailed

    seeded = await user_factory()
    user = await load_user(seeded)
    account = await cash.create_account(
        db_session, user, name="GX", account_type=CashAccountType.GXBANK
    )
    inflow = await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=Decimal("1000"),
        movement_date=date(2026, 1, 5),
    )
    with pytest.raises(ValidationFailed):
        await cash.update_movement(
            db_session, user, inflow.id, amount_myr=Decimal("-5")
        )


async def test_archived_account_excluded_from_buffer_math(
    db_session,
    user_factory: UserFactory,
    load_user: UserLoader,
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
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
        amount_myr=Decimal("4000"),
        movement_date=date(2026, 1, 5),
    )
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "4000.0000"
    )
    # Archiving removes the account from list_accounts (and so from buffer math
    # and total cash) while preserving its movement history.
    await cash.archive_account(db_session, user, account.id)
    assert await cash.deployable_surplus_myr(db_session, user) == Decimal(
        "0.0000"
    )
    assert await cash.total_cash_myr(db_session, user) == Decimal("0.0000")

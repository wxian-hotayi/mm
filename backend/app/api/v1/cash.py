"""Cash Buffer System endpoints (DESIGN §19.1, §19.7).

Operational MYR cash accounts and their movement ledger. All routes are
JWT-guarded and per-user isolated (services enforce ``user_id`` on every
query); mutations are audited inside the service layer. Balances, total cash,
deployable surplus, buffer fill and readiness are all **derived** (never
stored) — exposed through ``GET /cash/summary``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbDep
from app.schemas.cash import (
    CashAccountBalanceOut,
    CashAccountIn,
    CashAccountOut,
    CashAccountUpdate,
    CashMovementIn,
    CashMovementOut,
    CashMovementUpdate,
    CashSummaryOut,
)
from app.services import cash, deployment
from app.utils.dates import kl_today
from app.utils.money import ZERO

router = APIRouter(prefix="/cash", tags=["cash"])


async def _maybe_enqueue_threshold(db: DbDep, user: CurrentUser) -> None:
    """Auto-enqueue a THRESHOLD deployment intent after a cash mutation (§19.1).

    Idempotent and a no-op outside an open window / below the deploy threshold;
    runs after every movement create/update/delete so a surplus that first
    crosses the threshold inside an open deployment window is queued
    automatically (DESIGN §19.1)."""
    await deployment.maybe_enqueue_threshold(db, user)


# --------------------------------------------------------------------------- #
# Accounts                                                                     #
# --------------------------------------------------------------------------- #
@router.get("/accounts", response_model=list[CashAccountOut])
async def list_cash_accounts(
    db: DbDep,
    user: CurrentUser,
    include_archived: Annotated[bool, Query()] = False,
) -> list[CashAccountOut]:
    """List the user's cash accounts (archived excluded unless requested)."""
    accounts = await cash.list_accounts(
        db, user, include_archived=include_archived
    )
    return [CashAccountOut.from_row(account) for account in accounts]


@router.post(
    "/accounts",
    response_model=CashAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cash_account(
    payload: CashAccountIn, db: DbDep, user: CurrentUser
) -> CashAccountOut:
    """Create an operational cash account (GXBank, savings, emergency fund...)."""
    account = await cash.create_account(
        db,
        user,
        name=payload.name,
        account_type=payload.account_type,
        currency=payload.currency,
        is_buffer_source=payload.is_buffer_source,
        target_buffer_myr=payload.target_buffer_myr,
        annual_interest_pct=payload.annual_interest_pct,
        sort_order=payload.sort_order,
    )
    return CashAccountOut.from_row(account)


@router.patch("/accounts/{account_id}", response_model=CashAccountOut)
async def update_cash_account(
    account_id: int,
    payload: CashAccountUpdate,
    db: DbDep,
    user: CurrentUser,
) -> CashAccountOut:
    """Partially update a cash account (balances are derived, never touched)."""
    account = await cash.update_account(
        db,
        user,
        account_id,
        name=payload.name,
        account_type=payload.account_type,
        is_buffer_source=payload.is_buffer_source,
        target_buffer_myr=payload.target_buffer_myr,
        annual_interest_pct=payload.annual_interest_pct,
        sort_order=payload.sort_order,
    )
    return CashAccountOut.from_row(account)


@router.delete("/accounts/{account_id}", response_model=CashAccountOut)
async def archive_cash_account(
    account_id: int, db: DbDep, user: CurrentUser
) -> CashAccountOut:
    """Soft-delete (archive) a cash account; movement history is preserved."""
    account = await cash.archive_account(db, user, account_id)
    return CashAccountOut.from_row(account)


# --------------------------------------------------------------------------- #
# Movements                                                                    #
# --------------------------------------------------------------------------- #
@router.get("/movements", response_model=list[CashMovementOut])
async def list_cash_movements(
    db: DbDep,
    user: CurrentUser,
    account_id: Annotated[int | None, Query(gt=0)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[CashMovementOut]:
    """List the user's cash movements with optional account/date filters."""
    movements = await cash.list_movements(
        db,
        user,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
    )
    return [CashMovementOut.from_row(movement) for movement in movements]


@router.post(
    "/movements",
    response_model=CashMovementOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cash_movement(
    payload: CashMovementIn, db: DbDep, user: CurrentUser
) -> CashMovementOut:
    """Record a cash movement (ledger-first; balances re-derive on read)."""
    movement = await cash.create_movement(
        db,
        user,
        account_id=payload.account_id,
        movement_type=payload.movement_type,
        amount_myr=payload.amount_myr,
        movement_date=payload.movement_date,
        counterparty_account_id=payload.counterparty_account_id,
        linked_transaction_id=payload.linked_transaction_id,
        notes=payload.notes,
    )
    await _maybe_enqueue_threshold(db, user)
    return CashMovementOut.from_row(movement)


@router.patch("/movements/{movement_id}", response_model=CashMovementOut)
async def update_cash_movement(
    movement_id: int,
    payload: CashMovementUpdate,
    db: DbDep,
    user: CurrentUser,
) -> CashMovementOut:
    """Partially update a cash movement (structural links are immutable here)."""
    movement = await cash.update_movement(
        db,
        user,
        movement_id,
        movement_type=payload.movement_type,
        amount_myr=payload.amount_myr,
        movement_date=payload.movement_date,
        notes=payload.notes,
    )
    await _maybe_enqueue_threshold(db, user)
    return CashMovementOut.from_row(movement)


@router.delete(
    "/movements/{movement_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_cash_movement(
    movement_id: int, db: DbDep, user: CurrentUser
) -> None:
    """Delete a cash movement (balances re-derive from the remaining ledger)."""
    await cash.delete_movement(db, user, movement_id)
    await _maybe_enqueue_threshold(db, user)


# --------------------------------------------------------------------------- #
# Summary (derived)                                                            #
# --------------------------------------------------------------------------- #
@router.get("/summary", response_model=CashSummaryOut)
async def cash_summary(
    db: DbDep,
    user: CurrentUser,
    as_of: Annotated[date | None, Query()] = None,
) -> CashSummaryOut:
    """Derived cash position: per-account balances, total, deployable surplus,
    buffer fill ratio and deployment readiness (§19.1). Nothing is stored."""
    reference_date = as_of if as_of is not None else kl_today()
    accounts = await cash.list_accounts(db, user)
    account_balances = await cash.balances(db, user, as_of)
    total = await cash.total_cash_myr(db, user, as_of)
    deployable = await cash.deployable_surplus_myr(db, user, as_of)
    fill_ratio = await cash.buffer_fill_ratio(db, user, as_of)
    readiness = await cash.readiness(db, user, as_of)

    account_views = [
        CashAccountBalanceOut(
            **CashAccountOut.from_row(account).model_dump(),
            balance_myr=account_balances.get(account.id, ZERO),
        )
        for account in accounts
    ]
    return CashSummaryOut(
        accounts=account_views,
        total_cash_myr=total,
        deployable_surplus_myr=deployable,
        buffer_fill_ratio=fill_ratio,
        readiness=readiness,
        as_of=reference_date,
    )

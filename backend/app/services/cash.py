"""Cash Buffer System — operational MYR cash accounts and movement ledger.

The operational truth for the MYR side of the wealth flow
(*salary -> GXBank (accumulate) -> transfer to Moomoo (FX) -> broker DEPOSIT
-> BUY*, DESIGN §19.1). This layer is **ledger-first** exactly like the
transaction ledger: account balances, total cash, deployable surplus, buffer
fill and deployment readiness are ALWAYS derived from :class:`CashMovement`
rows — never stored as authoritative state.

All amounts are MYR :class:`decimal.Decimal` (exact, 4dp via
:class:`app.db.types.Money`). Movement amounts are stored positive; the sign is
applied by :class:`~app.models.cash.CashMovementType` in the balance
derivation::

    balance = Σ(INFLOW + INTEREST + TRANSFER_IN)
            − Σ(OUTFLOW + TRANSFER_OUT_TO_BROKER)
            ± ADJUSTMENT (signed by the caller via a leading '-')

Every mutation writes an :class:`~app.models.audit.AuditLog` row and the
service commits atomically. Every query is filtered by ``user_id``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.cash import CashAccount, CashAccountType, CashMovement, CashMovementType
from app.models.ips import IpsRule
from app.models.transaction import Transaction
from app.models.user import User
from app.utils.money import Q4, ZERO, safe_div

# Movement types that increase an account balance (everything else decreases it,
# except ADJUSTMENT which is signed by the stored amount via the caller).
_POSITIVE_TYPES: Final[frozenset[str]] = frozenset(
    {
        CashMovementType.INFLOW.value,
        CashMovementType.INTEREST.value,
        CashMovementType.TRANSFER_IN.value,
    }
)
_NEGATIVE_TYPES: Final[frozenset[str]] = frozenset(
    {
        CashMovementType.OUTFLOW.value,
        CashMovementType.TRANSFER_OUT_TO_BROKER.value,
    }
)

ReadinessState = Literal["READY", "ACCUMULATING"]


# --------------------------------------------------------------------------- #
# Audit helper                                                                 #
# --------------------------------------------------------------------------- #
def _audit(
    user_id: int,
    action: str,
    entity: str,
    entity_id: int | None,
    description: str,
    context: dict[str, object],
) -> AuditLog:
    """Build an INFO ``AUDIT`` log row for a cash-system mutation."""
    return AuditLog(
        user_id=user_id,
        event_type=AuditEventType.AUDIT.value,
        action=action,
        severity=AuditSeverity.INFO.value,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        description=description,
        context=json.dumps(context, default=str),
    )


# --------------------------------------------------------------------------- #
# Account CRUD                                                                 #
# --------------------------------------------------------------------------- #
async def get_account(
    db: AsyncSession, user: User, account_id: int
) -> CashAccount:
    """Fetch one of the user's cash accounts; raise NotFoundError otherwise."""
    result = await db.execute(
        select(CashAccount).where(
            CashAccount.id == account_id,
            CashAccount.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise NotFoundError(f"Cash account {account_id} not found")
    return account


async def list_accounts(
    db: AsyncSession, user: User, *, include_archived: bool = False
) -> list[CashAccount]:
    """List the user's cash accounts ordered by ``(sort_order, id)``.

    Archived accounts are excluded unless ``include_archived`` is set.
    """
    criteria = [CashAccount.user_id == user.id]
    if not include_archived:
        criteria.append(CashAccount.is_archived.is_(False))
    result = await db.execute(
        select(CashAccount)
        .where(*criteria)
        .order_by(CashAccount.sort_order, CashAccount.id)
    )
    return list(result.scalars().all())


async def create_account(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    account_type: CashAccountType | str,
    currency: str = "MYR",
    is_buffer_source: bool = False,
    target_buffer_myr: Decimal = ZERO,
    annual_interest_pct: Decimal = ZERO,
    sort_order: int = 0,
) -> CashAccount:
    """Create a cash account for the user, audit it and commit.

    ``target_buffer_myr`` (MYR) is the minimum kept and never deployable;
    ``annual_interest_pct`` is informational (pp). Both must be non-negative.
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValidationFailed("Cash account name must not be empty")
    type_value = (
        account_type.value
        if isinstance(account_type, CashAccountType)
        else str(account_type)
    )
    if type_value not in {member.value for member in CashAccountType}:
        raise ValidationFailed(f"Unknown cash account type {type_value!r}")
    if target_buffer_myr < ZERO:
        raise ValidationFailed("target_buffer_myr must be non-negative")
    if annual_interest_pct < ZERO:
        raise ValidationFailed("annual_interest_pct must be non-negative")

    account = CashAccount(
        user_id=user.id,
        name=clean_name,
        account_type=type_value,
        currency=currency,
        is_buffer_source=is_buffer_source,
        target_buffer_myr=Q4(target_buffer_myr),
        annual_interest_pct=Q4(annual_interest_pct),
        sort_order=sort_order,
    )
    db.add(account)
    await db.flush()
    db.add(
        _audit(
            user.id,
            "CASH_ACCOUNT_CREATE",
            "cash_account",
            account.id,
            f"Created cash account {clean_name!r}",
            {
                "account_type": type_value,
                "is_buffer_source": is_buffer_source,
                "target_buffer_myr": account.target_buffer_myr,
            },
        )
    )
    await db.commit()
    await db.refresh(account)
    return account


async def update_account(
    db: AsyncSession,
    user: User,
    account_id: int,
    *,
    name: str | None = None,
    account_type: CashAccountType | str | None = None,
    is_buffer_source: bool | None = None,
    target_buffer_myr: Decimal | None = None,
    annual_interest_pct: Decimal | None = None,
    sort_order: int | None = None,
) -> CashAccount:
    """Partially update a cash account, audit the change and commit.

    Only the fields supplied (non-``None``) are mutated. Balances are derived,
    so they are never touched here.
    """
    account = await get_account(db, user, account_id)
    changes: dict[str, object] = {}
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationFailed("Cash account name must not be empty")
        account.name = clean_name
        changes["name"] = clean_name
    if account_type is not None:
        type_value = (
            account_type.value
            if isinstance(account_type, CashAccountType)
            else str(account_type)
        )
        if type_value not in {member.value for member in CashAccountType}:
            raise ValidationFailed(f"Unknown cash account type {type_value!r}")
        account.account_type = type_value
        changes["account_type"] = type_value
    if is_buffer_source is not None:
        account.is_buffer_source = is_buffer_source
        changes["is_buffer_source"] = is_buffer_source
    if target_buffer_myr is not None:
        if target_buffer_myr < ZERO:
            raise ValidationFailed("target_buffer_myr must be non-negative")
        account.target_buffer_myr = Q4(target_buffer_myr)
        changes["target_buffer_myr"] = account.target_buffer_myr
    if annual_interest_pct is not None:
        if annual_interest_pct < ZERO:
            raise ValidationFailed("annual_interest_pct must be non-negative")
        account.annual_interest_pct = Q4(annual_interest_pct)
        changes["annual_interest_pct"] = account.annual_interest_pct
    if sort_order is not None:
        account.sort_order = sort_order
        changes["sort_order"] = sort_order

    db.add(
        _audit(
            user.id,
            "CASH_ACCOUNT_UPDATE",
            "cash_account",
            account.id,
            f"Updated cash account {account.id}",
            changes,
        )
    )
    await db.commit()
    await db.refresh(account)
    return account


async def archive_account(
    db: AsyncSession, user: User, account_id: int
) -> CashAccount:
    """Soft-delete a cash account (``is_archived = True``); preserves history.

    Movements are retained so historical balances stay reconstructable. An
    archived account is excluded from buffer/deployable math via
    :func:`list_accounts` (which the derived helpers use).
    """
    account = await get_account(db, user, account_id)
    account.is_archived = True
    db.add(
        _audit(
            user.id,
            "CASH_ACCOUNT_ARCHIVE",
            "cash_account",
            account.id,
            f"Archived cash account {account.id}",
            {"name": account.name},
        )
    )
    await db.commit()
    await db.refresh(account)
    return account


# --------------------------------------------------------------------------- #
# Movement CRUD                                                                #
# --------------------------------------------------------------------------- #
async def get_movement(
    db: AsyncSession, user: User, movement_id: int
) -> CashMovement:
    """Fetch one of the user's cash movements; raise NotFoundError otherwise."""
    result = await db.execute(
        select(CashMovement).where(
            CashMovement.id == movement_id,
            CashMovement.user_id == user.id,
        )
    )
    movement = result.scalar_one_or_none()
    if movement is None:
        raise NotFoundError(f"Cash movement {movement_id} not found")
    return movement


async def list_movements(
    db: AsyncSession,
    user: User,
    *,
    account_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[CashMovement]:
    """List the user's cash movements in ``(movement_date, id)`` order.

    Optional filters: a single ``account_id`` and an inclusive
    ``[date_from, date_to]`` range.
    """
    criteria = [CashMovement.user_id == user.id]
    if account_id is not None:
        criteria.append(CashMovement.account_id == account_id)
    if date_from is not None:
        criteria.append(CashMovement.movement_date >= date_from)
    if date_to is not None:
        criteria.append(CashMovement.movement_date <= date_to)
    result = await db.execute(
        select(CashMovement)
        .where(*criteria)
        .order_by(CashMovement.movement_date, CashMovement.id)
    )
    return list(result.scalars().all())


async def _ensure_owned_transaction(
    db: AsyncSession, user: User, transaction_id: int
) -> None:
    """Verify ``transaction_id`` belongs to ``user``; raise NotFoundError else.

    Used to scope ``linked_transaction_id`` on a cash movement so the FK can
    never point at another user's transaction (per-user isolation, §15).
    """
    result = await db.execute(
        select(Transaction.id).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"Transaction {transaction_id} not found")


async def create_movement(
    db: AsyncSession,
    user: User,
    *,
    account_id: int,
    movement_type: CashMovementType | str,
    amount_myr: Decimal,
    movement_date: date,
    counterparty_account_id: int | None = None,
    linked_transaction_id: int | None = None,
    notes: str = "",
) -> CashMovement:
    """Record a cash movement, audit it and commit atomically.

    Transfer semantics (DESIGN §19.1): a cash<->cash transfer is modelled as a
    *pair* of linked single-leg movements created via :func:`transfer`. A
    ``TRANSFER_IN`` / ``TRANSFER_OUT_TO_BROKER`` created here directly stores
    ``counterparty_account_id`` to record the other leg. ``amount_myr`` is
    stored positive; for an ``ADJUSTMENT`` the sign is carried in the stored
    amount (a negative amount subtracts, a positive amount adds).
    """
    account = await get_account(db, user, account_id)
    type_value = (
        movement_type.value
        if isinstance(movement_type, CashMovementType)
        else str(movement_type)
    )
    if type_value not in {member.value for member in CashMovementType}:
        raise ValidationFailed(f"Unknown cash movement type {type_value!r}")
    # ADJUSTMENT may be signed (it represents a correction); all other types
    # carry an unsigned magnitude and must be strictly positive.
    if type_value == CashMovementType.ADJUSTMENT.value:
        if amount_myr == ZERO:
            raise ValidationFailed("ADJUSTMENT amount must be non-zero")
    elif amount_myr <= ZERO:
        raise ValidationFailed(
            f"{type_value} amount_myr must be positive (got {amount_myr})"
        )
    if counterparty_account_id is not None:
        # Ensure the counterparty belongs to the same user.
        await get_account(db, user, counterparty_account_id)
    if linked_transaction_id is not None:
        # A linked broker transaction must belong to the same user — enforce
        # per-user isolation on the FK so a movement can never reference another
        # user's transaction (§15 data isolation).
        await _ensure_owned_transaction(db, user, linked_transaction_id)

    movement = CashMovement(
        user_id=user.id,
        account_id=account.id,
        movement_date=movement_date,
        movement_type=type_value,
        amount_myr=Q4(amount_myr),
        counterparty_account_id=counterparty_account_id,
        linked_transaction_id=linked_transaction_id,
        notes=notes,
    )
    db.add(movement)
    await db.flush()
    db.add(
        _audit(
            user.id,
            "CASH_MOVEMENT_CREATE",
            "cash_movement",
            movement.id,
            f"Recorded {type_value} of {movement.amount_myr} MYR",
            {
                "account_id": account.id,
                "movement_type": type_value,
                "amount_myr": movement.amount_myr,
                "movement_date": movement_date,
                "counterparty_account_id": counterparty_account_id,
                "linked_transaction_id": linked_transaction_id,
            },
        )
    )
    await db.commit()
    await db.refresh(movement)
    return movement


async def transfer(
    db: AsyncSession,
    user: User,
    *,
    from_account_id: int,
    to_account_id: int,
    amount_myr: Decimal,
    movement_date: date,
    notes: str = "",
) -> tuple[CashMovement, CashMovement]:
    """Move ``amount_myr`` between two of the user's cash accounts.

    Creates two linked single-leg movements (DESIGN §19.1): a
    ``TRANSFER_OUT_TO_BROKER`` leg on the source (decreases its balance) and a
    ``TRANSFER_IN`` leg on the destination (increases its balance), each
    pointing at the other via ``counterparty_account_id``. Returns
    ``(out_leg, in_leg)``. Audited and committed atomically.
    """
    if from_account_id == to_account_id:
        raise ValidationFailed("Cannot transfer to the same account")
    if amount_myr <= ZERO:
        raise ValidationFailed("Transfer amount_myr must be positive")
    source = await get_account(db, user, from_account_id)
    destination = await get_account(db, user, to_account_id)
    amount = Q4(amount_myr)

    out_leg = CashMovement(
        user_id=user.id,
        account_id=source.id,
        movement_date=movement_date,
        movement_type=CashMovementType.TRANSFER_OUT_TO_BROKER.value,
        amount_myr=amount,
        counterparty_account_id=destination.id,
        notes=notes,
    )
    in_leg = CashMovement(
        user_id=user.id,
        account_id=destination.id,
        movement_date=movement_date,
        movement_type=CashMovementType.TRANSFER_IN.value,
        amount_myr=amount,
        counterparty_account_id=source.id,
        notes=notes,
    )
    db.add_all([out_leg, in_leg])
    await db.flush()
    # Pair the legs so each references the other movement row explicitly.
    db.add(
        _audit(
            user.id,
            "CASH_TRANSFER",
            "cash_movement",
            out_leg.id,
            f"Transferred {amount} MYR from account {source.id} to "
            f"{destination.id}",
            {
                "from_account_id": source.id,
                "to_account_id": destination.id,
                "amount_myr": amount,
                "out_movement_id": out_leg.id,
                "in_movement_id": in_leg.id,
                "movement_date": movement_date,
            },
        )
    )
    await db.commit()
    await db.refresh(out_leg)
    await db.refresh(in_leg)
    return out_leg, in_leg


async def update_movement(
    db: AsyncSession,
    user: User,
    movement_id: int,
    *,
    movement_type: CashMovementType | str | None = None,
    amount_myr: Decimal | None = None,
    movement_date: date | None = None,
    notes: str | None = None,
) -> CashMovement:
    """Partially update a cash movement, audit and commit.

    Only the supplied fields are changed. ``counterparty_account_id`` /
    ``linked_transaction_id`` are structural links and are not edited here (use
    :func:`transfer` for transfers). Amount sign rules match
    :func:`create_movement`.
    """
    movement = await get_movement(db, user, movement_id)
    changes: dict[str, object] = {}
    new_type = movement.movement_type
    if movement_type is not None:
        new_type = (
            movement_type.value
            if isinstance(movement_type, CashMovementType)
            else str(movement_type)
        )
        if new_type not in {member.value for member in CashMovementType}:
            raise ValidationFailed(f"Unknown cash movement type {new_type!r}")
        movement.movement_type = new_type
        changes["movement_type"] = new_type
    if amount_myr is not None:
        if new_type == CashMovementType.ADJUSTMENT.value:
            if amount_myr == ZERO:
                raise ValidationFailed("ADJUSTMENT amount must be non-zero")
        elif amount_myr <= ZERO:
            raise ValidationFailed(
                f"{new_type} amount_myr must be positive (got {amount_myr})"
            )
        movement.amount_myr = Q4(amount_myr)
        changes["amount_myr"] = movement.amount_myr
    if movement_date is not None:
        movement.movement_date = movement_date
        changes["movement_date"] = movement_date
    if notes is not None:
        movement.notes = notes
        changes["notes"] = notes

    db.add(
        _audit(
            user.id,
            "CASH_MOVEMENT_UPDATE",
            "cash_movement",
            movement.id,
            f"Updated cash movement {movement.id}",
            changes,
        )
    )
    await db.commit()
    await db.refresh(movement)
    return movement


async def delete_movement(
    db: AsyncSession, user: User, movement_id: int
) -> None:
    """Delete a cash movement after auditing it, then commit.

    Note: deleting one leg of a transfer leaves the paired leg intact; callers
    that need both legs removed should delete each explicitly.
    """
    movement = await get_movement(db, user, movement_id)
    db.add(
        _audit(
            user.id,
            "CASH_MOVEMENT_DELETE",
            "cash_movement",
            movement.id,
            f"Deleted cash movement {movement.id}",
            {
                "account_id": movement.account_id,
                "movement_type": movement.movement_type,
                "amount_myr": movement.amount_myr,
            },
        )
    )
    await db.delete(movement)
    await db.commit()


# --------------------------------------------------------------------------- #
# Derived balances and surplus (never stored)                                 #
# --------------------------------------------------------------------------- #
def _signed_amount(movement: CashMovement) -> Decimal:
    """Return the signed MYR contribution of one movement to its balance.

    ``+`` for INFLOW/INTEREST/TRANSFER_IN, ``−`` for OUTFLOW/
    TRANSFER_OUT_TO_BROKER; an ADJUSTMENT carries its own sign in the stored
    amount (the stored value is added as-is).
    """
    amount = Q4(movement.amount_myr)
    if movement.movement_type in _POSITIVE_TYPES:
        return amount
    if movement.movement_type in _NEGATIVE_TYPES:
        return -amount
    # ADJUSTMENT: signed by the stored amount.
    return amount


async def balance(
    db: AsyncSession,
    user: User,
    account_id: int,
    as_of: date | None = None,
) -> Decimal:
    """Derive an account's MYR balance from its movement ledger (§19.1).

    ``balance = Σ(INFLOW + INTEREST + TRANSFER_IN) − Σ(OUTFLOW +
    TRANSFER_OUT_TO_BROKER) ± ADJUSTMENT``. ``as_of`` limits to movements on or
    before that date. Verifies the account belongs to the user. Returns a 4dp
    MYR :class:`~decimal.Decimal` (never stored).
    """
    await get_account(db, user, account_id)
    movements = await list_movements(
        db, user, account_id=account_id, date_to=as_of
    )
    return Q4(sum((_signed_amount(m) for m in movements), start=ZERO))


async def balances(
    db: AsyncSession,
    user: User,
    as_of: date | None = None,
    *,
    include_archived: bool = False,
) -> dict[int, Decimal]:
    """Derive the MYR balance of every (non-archived) account for the user.

    Returns ``{account_id: balance_myr}`` covering all accounts from
    :func:`list_accounts` (zero for accounts with no movements ≤ ``as_of``).
    Computed in a single movement pass — O(movements), one query.
    """
    accounts = await list_accounts(
        db, user, include_archived=include_archived
    )
    result: dict[int, Decimal] = {account.id: ZERO for account in accounts}
    if not result:
        return result
    criteria = [CashMovement.user_id == user.id]
    if as_of is not None:
        criteria.append(CashMovement.movement_date <= as_of)
    rows = await db.execute(select(CashMovement).where(*criteria))
    for movement in rows.scalars().all():
        if movement.account_id in result:
            result[movement.account_id] += _signed_amount(movement)
    return {account_id: Q4(value) for account_id, value in result.items()}


async def total_cash_myr(
    db: AsyncSession,
    user: User,
    as_of: date | None = None,
    *,
    include_archived: bool = False,
) -> Decimal:
    """Total MYR across all (non-archived) cash accounts as of ``as_of``."""
    account_balances = await balances(
        db, user, as_of, include_archived=include_archived
    )
    return Q4(sum(account_balances.values(), start=ZERO))


def _buffer_source_targets(
    accounts: Sequence[CashAccount],
) -> Decimal:
    """Sum the ``target_buffer_myr`` of buffer-source accounts (4dp MYR)."""
    return Q4(
        sum(
            (
                Q4(account.target_buffer_myr)
                for account in accounts
                if account.is_buffer_source
            ),
            start=ZERO,
        )
    )


async def _buffer_source_balance(
    db: AsyncSession, user: User, as_of: date | None
) -> tuple[Decimal, Decimal]:
    """Return ``(Σ buffer-source balance, Σ buffer-source target)`` in MYR.

    Only non-archived buffer-source accounts (e.g. GXBank) count toward
    deployable investment cash; the emergency fund is excluded by design.
    """
    accounts = await list_accounts(db, user)
    account_balances = await balances(db, user, as_of)
    source_balance = Q4(
        sum(
            (
                account_balances.get(account.id, ZERO)
                for account in accounts
                if account.is_buffer_source
            ),
            start=ZERO,
        )
    )
    source_target = _buffer_source_targets(accounts)
    return source_balance, source_target


async def deployable_surplus_myr(
    db: AsyncSession, user: User, as_of: date | None = None
) -> Decimal:
    """MYR cash available to deploy: ``max(0, Σ buffer-source balance −
    Σ buffer-source target_buffer_myr)`` (DESIGN §19.1).

    Target buffers are reserves that are never deployable. Returns a 4dp MYR
    :class:`~decimal.Decimal`; never negative, never stored.
    """
    source_balance, source_target = await _buffer_source_balance(
        db, user, as_of
    )
    surplus = source_balance - source_target
    return surplus if surplus > ZERO else ZERO


async def buffer_fill_ratio(
    db: AsyncSession, user: User, as_of: date | None = None
) -> Decimal | None:
    """Ratio of buffer-source balance to target buffer: ``Σ balance /
    Σ target_buffer_myr`` (clamped at 0 below, unbounded above; §19.1).

    Returns ``None`` when no target buffer is configured (the ratio is
    undefined — division guarded). A value ≥ 1 means buffers are fully funded.
    """
    source_balance, source_target = await _buffer_source_balance(
        db, user, as_of
    )
    if source_target <= ZERO:
        return None
    ratio = safe_div(source_balance, source_target)
    return Q4(ratio if ratio > ZERO else ZERO)


async def _min_deploy_threshold(db: AsyncSession, user: User) -> Decimal:
    """Read the user's ``min_deploy_threshold_myr`` from their IPS policy.

    Raises :class:`NotFoundError` when no IPS policy row exists (seeded per
    user at registration via ``init_db.ensure_default_ips``).
    """
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(
            "No Investment Policy Statement found for this user"
        )
    return Q4(ips.min_deploy_threshold_myr)


async def readiness(
    db: AsyncSession, user: User, as_of: date | None = None
) -> ReadinessState:
    """Deployment-readiness state used by §19.2/§19.3.

    ``READY`` when ``deployable_surplus_myr ≥ min_deploy_threshold_myr``
    (a meaningful Moomoo buy), else ``ACCUMULATING``.
    """
    surplus = await deployable_surplus_myr(db, user, as_of)
    threshold = await _min_deploy_threshold(db, user)
    return "READY" if surplus >= threshold else "ACCUMULATING"

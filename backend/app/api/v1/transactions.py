"""Transaction ledger CRUD with full-ledger re-validation on every mutation.

Every query is scoped to the authenticated user's ``user_id`` — personal
finance isolation applies to admins as well (an admin sees only their own
ledger). Every mutation writes an audit log row, and creates additionally
run the behavior engine (without prices) and return its warnings.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import ValidationError
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.deps import CurrentUser, DbDep
from app.core.errors import NotFoundError, ValidationFailed
from app.db.types import MONEY_PRECISION, MONEY_SCALE
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.schemas.common import Paginated
from app.schemas.transaction import (
    TransactionIn,
    TransactionOut,
    TransactionUpdate,
    TransactionWithWarningsOut,
)
from app.services import behavior, ips_enforcement, ledger
from app.utils.money import ZERO

router = APIRouter(prefix="/transactions", tags=["transactions"])

_TRADE_TYPE_VALUES = frozenset(
    {TransactionType.BUY.value, TransactionType.SELL.value}
)

_SORT_COLUMNS: dict[str, InstrumentedAttribute[object]] = {
    "transaction_date": Transaction.transaction_date,
    "asset_symbol": Transaction.asset_symbol,
    "transaction_type": Transaction.transaction_type,
}

SortField = Literal[
    "transaction_date", "total_amount_myr", "asset_symbol", "transaction_type"
]


async def load_user_transactions(
    db: AsyncSession, user_id: int
) -> list[Transaction]:
    """Load a user's full ledger in deterministic (date, id) order."""
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    return list(result.scalars().all())


async def _get_owned_transaction(
    db: AsyncSession, user_id: int, transaction_id: int
) -> Transaction:
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
        )
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id} not found")
    return txn


def _row_from_payload(payload: TransactionIn, user_id: int) -> Transaction:
    return Transaction(
        user_id=user_id,
        transaction_date=payload.transaction_date,
        transaction_type=payload.transaction_type.value,
        asset_symbol=payload.asset_symbol,
        quantity=payload.quantity,
        unit_price_usd=payload.unit_price_usd,
        fee_usd=payload.fee_usd,
        fx_rate_recorded=payload.fx_rate_recorded,
        total_amount_myr=payload.stored_total_amount_myr(),
        notes=payload.notes,
    )


def _apply_payload(txn: Transaction, payload: TransactionIn) -> None:
    txn.transaction_date = payload.transaction_date
    txn.transaction_type = payload.transaction_type.value
    txn.asset_symbol = payload.asset_symbol
    txn.quantity = payload.quantity
    txn.unit_price_usd = payload.unit_price_usd
    txn.fee_usd = payload.fee_usd
    txn.fx_rate_recorded = payload.fx_rate_recorded
    txn.total_amount_myr = payload.stored_total_amount_myr()
    txn.notes = payload.notes


def _merged_payload(
    txn: Transaction, update: TransactionUpdate
) -> TransactionIn:
    """Merge a partial update onto a stored row and re-validate the result
    as a complete :class:`TransactionIn`.

    Fields that do not apply to the merged transaction type (e.g. quantity
    when switching a BUY to a DEPOSIT) are dropped from the stored row, so
    family switches must supply the newly required fields explicitly.
    """
    update_data = update.model_dump(exclude_unset=True)
    target_type = TransactionType(
        update_data.get("transaction_type", txn.transaction_type)
    )
    target_is_trade = target_type.value in _TRADE_TYPE_VALUES
    current_is_trade = txn.transaction_type in _TRADE_TYPE_VALUES

    base: dict[str, object] = {
        "transaction_type": target_type,
        "transaction_date": txn.transaction_date,
        "asset_symbol": txn.asset_symbol,
        "fee_usd": txn.fee_usd,
        "fx_rate_recorded": txn.fx_rate_recorded,
        "notes": txn.notes,
    }
    if target_is_trade and current_is_trade:
        base["quantity"] = txn.quantity
        base["unit_price_usd"] = txn.unit_price_usd
    elif not target_is_trade and not current_is_trade:
        base["total_amount_myr"] = txn.total_amount_myr
    if not target_is_trade:
        if "fee_usd" not in update_data:
            base["fee_usd"] = ZERO
        if (
            target_type is not TransactionType.DIVIDEND
            and "asset_symbol" not in update_data
        ):
            base["asset_symbol"] = None
        if (
            "amount_usd" in update_data
            and "total_amount_myr" not in update_data
        ):
            base.pop("total_amount_myr", None)

    merged = {**base, **update_data}
    try:
        return TransactionIn.model_validate(merged)
    except ValidationError as exc:
        raise ValidationFailed(
            "; ".join(error["msg"] for error in exc.errors())
        ) from exc


def _audit_row(
    user: User, action: str, txn: Transaction, detail: str
) -> AuditLog:
    return AuditLog(
        user_id=user.id,
        event_type=AuditEventType.AUDIT.value,
        action=action,
        severity=AuditSeverity.INFO.value,
        entity="transaction",
        entity_id=str(txn.id),
        description=detail,
        context=json.dumps(
            {
                "transaction_type": txn.transaction_type,
                "transaction_date": txn.transaction_date.isoformat(),
                "asset_symbol": txn.asset_symbol,
                "quantity": txn.quantity,
                "unit_price_usd": txn.unit_price_usd,
                "fee_usd": txn.fee_usd,
                "fx_rate_recorded": txn.fx_rate_recorded,
                "total_amount_myr": txn.total_amount_myr,
                "notes": txn.notes,
            },
            default=str,
        ),
    )


def _warning_messages(flags: list[behavior.BehaviorFlag]) -> list[str]:
    return [f"{flag.title}: {flag.message}" for flag in flags]


def _ips_action(payload: TransactionIn) -> dict[str, object]:
    """Build the IPS-enforcement action mapping for a candidate transaction."""
    return {
        "kind": ips_enforcement.ACTION_TRANSACTION,
        "type": payload.transaction_type.value,
        "asset_symbol": payload.asset_symbol,
        "date": payload.transaction_date.isoformat(),
    }


def _ips_warning_messages(
    violations: list[ips_enforcement.IpsViolation],
) -> list[str]:
    """Render INFO/WARN IPS violations as human-readable warning strings."""
    return [f"{v.rule_type} ({v.level}): {v.message}" for v in violations]


async def _enforce_ips(
    db: AsyncSession,
    user: User,
    payload: TransactionIn,
    override: bool,
) -> list[str]:
    """Validate a candidate transaction against the IPS policy (§19.5).

    A BLOCK-level violation without ``override`` raises
    :class:`ValidationFailed` (HTTP 422) after auditing each blocking
    violation; an audited ``override=True`` is the sole bypass. INFO/WARN
    violations never block — they are returned as warning strings to surface to
    the caller. Cash events (DEPOSIT/WITHDRAWAL/DIVIDEND/FEE) are never
    IPS-restricted, so the verdict is clean for them.
    """
    verdict = await ips_enforcement.validate_action(
        db, user, _ips_action(payload), override=override
    )
    if not verdict.allowed:
        for violation in verdict.violations:
            await ips_enforcement.record_block_audit(
                db, user, violation, override=False
            )
        await db.commit()
        messages = "; ".join(v.message for v in verdict.violations)
        raise ValidationFailed(
            f"Transaction blocked by IPS enforcement: {messages}"
        )
    if override and verdict.violations:
        for violation in verdict.violations:
            await ips_enforcement.record_block_audit(
                db, user, violation, override=True
            )
    return _ips_warning_messages(verdict.warnings)


@router.post(
    "",
    response_model=TransactionWithWarningsOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    payload: TransactionIn,
    db: DbDep,
    user: CurrentUser,
    override: Annotated[bool, Query()] = False,
) -> TransactionWithWarningsOut:
    """Create a ledger transaction after validating the whole ledger
    including the new row (oversell protection, structural checks) and the IPS
    enforcement gate.

    A BLOCK-level IPS violation (forbidden asset class — leverage / options /
    non-allowed instrument) rejects the request with HTTP 422 unless
    ``override=true`` is supplied (audited bypass). INFO/WARN IPS violations and
    behavior flags never block; both are returned as warnings (§19.5)."""
    # IPS enforcement runs first so a forbidden-asset BUY is rejected before any
    # ledger work. INFO/WARN violations are surfaced as warnings.
    ips_warnings = await _enforce_ips(db, user, payload, override)
    new_row = _row_from_payload(payload, user.id)
    existing = await load_user_transactions(db, user.id)
    ledger.validate_ledger([*existing, new_row])
    db.add(new_row)
    await db.flush()
    db.add(
        _audit_row(
            user,
            "TRANSACTION_CREATE",
            new_row,
            f"Created {new_row.transaction_type} transaction",
        )
    )
    flags = await behavior.evaluate_and_record(
        db, user, [*existing, new_row]
    )
    await db.commit()
    await db.refresh(new_row)
    return TransactionWithWarningsOut.from_row_with_warnings(
        new_row, _warning_messages(flags), ips_warnings
    )


@router.get("", response_model=Paginated[TransactionOut])
async def list_transactions(
    db: DbDep,
    user: CurrentUser,
    transaction_type: Annotated[
        TransactionType | None, Query(alias="type")
    ] = None,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=256)] = None,
    sort: Annotated[SortField, Query()] = "transaction_date",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Paginated[TransactionOut]:
    """List the user's transactions with filtering, sorting and pagination.
    ``search`` matches the notes field (case-insensitive substring)."""
    criteria = [Transaction.user_id == user.id]
    if transaction_type is not None:
        criteria.append(
            Transaction.transaction_type == transaction_type.value
        )
    if symbol is not None and symbol.strip():
        criteria.append(Transaction.asset_symbol == symbol.strip().upper())
    if date_from is not None:
        criteria.append(Transaction.transaction_date >= date_from)
    if date_to is not None:
        criteria.append(Transaction.transaction_date <= date_to)
    if search is not None and search.strip():
        criteria.append(
            Transaction.notes.icontains(search.strip(), autoescape=True)
        )

    total = (
        await db.execute(
            select(func.count())
            .select_from(Transaction)
            .where(*criteria)
        )
    ).scalar_one()

    if sort == "total_amount_myr":
        # Money is TEXT-backed on SQLite; cast for numeric ordering.
        sort_expression = cast(
            Transaction.total_amount_myr,
            Numeric(MONEY_PRECISION, MONEY_SCALE),
        )
    else:
        sort_expression = _SORT_COLUMNS[sort]
    ordering = (
        (sort_expression.desc(), Transaction.id.desc())
        if order == "desc"
        else (sort_expression.asc(), Transaction.id.asc())
    )
    result = await db.execute(
        select(Transaction)
        .where(*criteria)
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        TransactionOut.from_row(txn) for txn in result.scalars().all()
    ]
    return Paginated[TransactionOut](
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(
    transaction_id: int, db: DbDep, user: CurrentUser
) -> TransactionOut:
    """Fetch one of the user's transactions by id."""
    txn = await _get_owned_transaction(db, user.id, transaction_id)
    return TransactionOut.from_row(txn)


@router.patch("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    transaction_id: int,
    update: TransactionUpdate,
    db: DbDep,
    user: CurrentUser,
    override: Annotated[bool, Query()] = False,
) -> TransactionOut:
    """Partially update a transaction, re-validating the full ledger with the
    edited row in place and re-running the IPS enforcement gate before
    committing.

    A BLOCK-level IPS violation on the merged transaction rejects with HTTP 422
    unless ``override=true`` is supplied (audited bypass); INFO/WARN violations
    never block (§19.5)."""
    txn = await _get_owned_transaction(db, user.id, transaction_id)
    payload = _merged_payload(txn, update)
    await _enforce_ips(db, user, payload, override)
    _apply_payload(txn, payload)
    all_rows = await load_user_transactions(db, user.id)
    ledger.validate_ledger(all_rows)
    db.add(
        _audit_row(
            user,
            "TRANSACTION_UPDATE",
            txn,
            f"Updated {txn.transaction_type} transaction {txn.id}",
        )
    )
    await db.commit()
    await db.refresh(txn)
    return TransactionOut.from_row(txn)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: int, db: DbDep, user: CurrentUser
) -> None:
    """Delete a transaction after validating that the remaining ledger
    stays consistent (no oversell appears downstream)."""
    txn = await _get_owned_transaction(db, user.id, transaction_id)
    all_rows = await load_user_transactions(db, user.id)
    remaining = [row for row in all_rows if row.id != txn.id]
    ledger.validate_ledger(remaining)
    db.add(
        _audit_row(
            user,
            "TRANSACTION_DELETE",
            txn,
            f"Deleted {txn.transaction_type} transaction {txn.id}",
        )
    )
    await db.delete(txn)
    await db.commit()

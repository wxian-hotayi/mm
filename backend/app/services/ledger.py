"""Ledger replay engine — THE source of all derived portfolio state.

Holdings, cash, realized gains, dividends, fees and net deposits are replayed
from the transactions ledger on demand; nothing derived is ever stored. USD
amounts for cash events are always derived from the authoritative MYR amount
and the FX rate recorded on the transaction — the engine never reads a stored
USD total.

All money values are Decimal: USD and MYR amounts quantized to 4 decimal
places (ROUND_HALF_UP) at each derivation step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.errors import ValidationFailed
from app.models.transaction import Transaction, TransactionType
from app.utils.money import Q4, ZERO, safe_div


@dataclass
class Position:
    """A held position in one symbol (quantities and basis in USD)."""

    symbol: str
    quantity: Decimal = ZERO
    cost_basis_usd: Decimal = ZERO

    @property
    def avg_cost_usd(self) -> Decimal:
        """Average cost per share in USD (4dp); ZERO when nothing is held."""
        if self.quantity == ZERO:
            return ZERO
        return Q4(self.cost_basis_usd / self.quantity)


@dataclass
class LedgerState:
    """Running state produced by replaying the transaction ledger.

    Units: ``*_usd`` fields are USD, ``*_myr`` fields are MYR.
    ``external_flows`` holds investor-perspective signed flows
    ``(date, usd, myr)``: deposits are negative (cash leaves the investor's
    pocket into the portfolio), withdrawals positive — ready for XIRR.
    ``warnings`` holds non-fatal data-quality messages (e.g. negative cash).
    """

    cash_usd: Decimal = ZERO
    positions: dict[str, Position] = field(default_factory=dict)
    realized_gain_usd: Decimal = ZERO
    dividends_usd: Decimal = ZERO
    fees_usd: Decimal = ZERO
    net_deposits_usd: Decimal = ZERO
    net_deposits_myr: Decimal = ZERO
    external_flows: list[tuple[date, Decimal, Decimal]] = field(
        default_factory=list
    )
    warnings: list[str] = field(default_factory=list)


def _sort_key(txn: Transaction) -> tuple[date, bool, int]:
    """Deterministic replay order: (transaction_date, id); unsaved rows
    (id None, i.e. a transaction being validated pre-commit) sort last
    within their date."""
    return (txn.transaction_date, txn.id is None, txn.id or 0)


def _describe(txn: Transaction) -> str:
    """Human-readable transaction reference used in error/warning messages."""
    symbol = f" {txn.asset_symbol}" if txn.asset_symbol else ""
    return (
        f"{txn.transaction_type}{symbol} on "
        f"{txn.transaction_date.isoformat()}"
    )


def _cash_amount_usd(txn: Transaction) -> Decimal:
    """Derive the USD amount of a cash event (DEPOSIT/WITHDRAWAL/DIVIDEND/FEE)
    from its authoritative MYR amount and recorded FX rate, quantized 4dp."""
    if txn.total_amount_myr is None or txn.total_amount_myr <= ZERO:
        raise ValidationFailed(
            f"{_describe(txn)} requires a positive total_amount_myr"
        )
    if txn.fx_rate_recorded is None or txn.fx_rate_recorded <= ZERO:
        raise ValidationFailed(
            f"{_describe(txn)} requires a positive fx_rate_recorded"
        )
    return Q4(safe_div(txn.total_amount_myr, txn.fx_rate_recorded))


def _trade_fields(txn: Transaction) -> tuple[str, Decimal, Decimal, Decimal]:
    """Validate and return (symbol, quantity, unit_price_usd, fee_usd) for a
    BUY/SELL transaction. Fee defaults to ZERO when unset pre-commit."""
    if not txn.asset_symbol:
        raise ValidationFailed(f"{_describe(txn)} requires an asset_symbol")
    if txn.quantity is None or txn.quantity <= ZERO:
        raise ValidationFailed(f"{_describe(txn)} requires a positive quantity")
    if txn.unit_price_usd is None or txn.unit_price_usd < ZERO:
        raise ValidationFailed(
            f"{_describe(txn)} requires a non-negative unit_price_usd"
        )
    fee = txn.fee_usd if txn.fee_usd is not None else ZERO
    if fee < ZERO:
        raise ValidationFailed(f"{_describe(txn)} has a negative fee_usd")
    return txn.asset_symbol, txn.quantity, txn.unit_price_usd, fee


def _apply(state: LedgerState, txn: Transaction) -> None:
    """Apply one transaction to the running state (mutates ``state``)."""
    txn_type = txn.transaction_type
    if txn_type == TransactionType.DEPOSIT.value:
        amount_usd = _cash_amount_usd(txn)
        amount_myr = Q4(txn.total_amount_myr)
        state.cash_usd += amount_usd
        state.net_deposits_usd += amount_usd
        state.net_deposits_myr += amount_myr
        state.external_flows.append(
            (txn.transaction_date, -amount_usd, -amount_myr)
        )
    elif txn_type == TransactionType.WITHDRAWAL.value:
        amount_usd = _cash_amount_usd(txn)
        amount_myr = Q4(txn.total_amount_myr)
        state.cash_usd -= amount_usd
        state.net_deposits_usd -= amount_usd
        state.net_deposits_myr -= amount_myr
        state.external_flows.append(
            (txn.transaction_date, amount_usd, amount_myr)
        )
    elif txn_type == TransactionType.DIVIDEND.value:
        if not txn.asset_symbol:
            raise ValidationFailed(
                f"{_describe(txn)} requires an asset_symbol"
            )
        amount_usd = _cash_amount_usd(txn)
        state.cash_usd += amount_usd
        state.dividends_usd += amount_usd
    elif txn_type == TransactionType.FEE.value:
        amount_usd = _cash_amount_usd(txn)
        state.cash_usd -= amount_usd
        state.fees_usd += amount_usd
    elif txn_type == TransactionType.BUY.value:
        symbol, quantity, price, fee = _trade_fields(txn)
        cost_usd = Q4(quantity * price + fee)
        state.cash_usd -= cost_usd
        position = state.positions.setdefault(symbol, Position(symbol=symbol))
        position.quantity += quantity
        position.cost_basis_usd += cost_usd
    elif txn_type == TransactionType.SELL.value:
        symbol, quantity, price, fee = _trade_fields(txn)
        position = state.positions.get(symbol)
        held = position.quantity if position is not None else ZERO
        if position is None or quantity > held:
            raise ValidationFailed(
                f"SELL of {quantity} {symbol} on "
                f"{txn.transaction_date.isoformat()} exceeds held quantity "
                f"({held} {symbol})"
            )
        proceeds_usd = Q4(quantity * price - fee)
        if quantity == position.quantity:
            cost_removed_usd = position.cost_basis_usd
        else:
            # Preserve the reported per-share average cost across a partial
            # sell (DESIGN section 7.1): carry the 4dp avg_cost (the
            # invariant), derive the retained basis as remaining_qty x that
            # frozen avg, and let any rounding crumb flow into the removed
            # basis (hence realized) rather than drifting the held avg_cost.
            frozen_avg_cost = position.avg_cost_usd
            remaining_qty = position.quantity - quantity
            retained_basis_usd = Q4(remaining_qty * frozen_avg_cost)
            cost_removed_usd = position.cost_basis_usd - retained_basis_usd
        state.realized_gain_usd += proceeds_usd - cost_removed_usd
        state.cash_usd += proceeds_usd
        position.quantity -= quantity
        position.cost_basis_usd -= cost_removed_usd
        if position.quantity == ZERO:
            del state.positions[symbol]
    else:
        raise ValidationFailed(
            f"Unknown transaction type {txn_type!r} on "
            f"{txn.transaction_date.isoformat()}"
        )


def replay(
    transactions: Sequence[Transaction], as_of: date | None = None
) -> LedgerState:
    """Replay the ledger in (transaction_date, id) order and return the
    resulting :class:`LedgerState`.

    ``as_of`` limits the replay to transactions dated on or before that date.
    Raises :class:`ValidationFailed` when a SELL exceeds the held quantity at
    its point in time or a row is structurally invalid. Cash going negative
    is NOT an error: a warning naming the date is appended instead (the
    broker ledger is authoritative; the data may simply be incomplete).
    """
    state = LedgerState()
    selected = [
        txn
        for txn in transactions
        if as_of is None or txn.transaction_date <= as_of
    ]
    for txn in sorted(selected, key=_sort_key):
        was_negative = state.cash_usd < ZERO
        _apply(state, txn)
        if state.cash_usd < ZERO and not was_negative:
            state.warnings.append(
                f"Cash balance went negative ({state.cash_usd} USD) after "
                f"{_describe(txn)}; check the ledger for missing deposits"
            )
    return state


def validate_ledger(transactions: Sequence[Transaction]) -> LedgerState:
    """Full-ledger validation used before committing a new or edited
    transaction: replays everything and raises :class:`ValidationFailed` on
    oversell or structurally invalid rows. Returns the final state so callers
    can surface its ``warnings``."""
    return replay(transactions)

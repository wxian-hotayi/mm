"""Pure ledger replay math: exact Decimal assertions on the canonical ledger.

Canonical ledger (full hand computation in conftest.canonical_transactions):
deposits RM13,350 @ 4.45 = $3,000 total; BUY 2 VOO @ $470 fee $1;
BUY 1 QQQ @ $400 fee $1; SELL 0.5 VOO @ $500 fee $1; DIVIDEND VOO $3.30;
FEE $1.10.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.models.transaction import Transaction
from app.services.ledger import replay, validate_ledger


def test_canonical_ledger_exact(
    canonical_ledger_rows: list[Transaction],
    canonical_expected: dict[str, Decimal],
) -> None:
    state = replay(canonical_ledger_rows)

    # Cash: 2000 + 1000 - 941 - 401 + 249 + 3.30 - 1.10 = 1,909.20.
    assert state.cash_usd == canonical_expected["cash_usd"]

    voo = state.positions["VOO"]
    assert voo.quantity == canonical_expected["voo_quantity"]
    assert voo.cost_basis_usd == canonical_expected["voo_cost_basis_usd"]
    # Average cost includes the buy fee: 941 / 2 = 470.50 per share.
    assert voo.avg_cost_usd == canonical_expected["voo_avg_cost_usd"]

    qqq = state.positions["QQQ"]
    assert qqq.quantity == canonical_expected["qqq_quantity"]
    assert qqq.cost_basis_usd == canonical_expected["qqq_cost_basis_usd"]
    assert qqq.avg_cost_usd == canonical_expected["qqq_avg_cost_usd"]

    # Realized on the partial sell: 0.5 x (500 - 470.50) - 1 = 13.75.
    assert state.realized_gain_usd == canonical_expected["realized_gain_usd"]
    assert state.dividends_usd == canonical_expected["dividends_usd"]
    assert state.fees_usd == canonical_expected["fees_usd"]
    assert state.net_deposits_usd == canonical_expected["net_deposits_usd"]
    assert state.net_deposits_myr == canonical_expected["net_deposits_myr"]
    assert state.warnings == []


def test_avg_cost_unchanged_by_partial_sell(
    canonical_ledger_rows: list[Transaction],
) -> None:
    # Before the sell (as-of 2026-03-16): 2 shares, basis 941, avg 470.50.
    before = replay(canonical_ledger_rows, as_of=date(2026, 3, 16))
    assert before.positions["VOO"].quantity == Decimal("2.0000")
    assert before.positions["VOO"].cost_basis_usd == Decimal("941.0000")
    assert before.positions["VOO"].avg_cost_usd == Decimal("470.5000")
    # After selling 0.5 sh: 1.5 sh, basis 705.75 — avg cost STILL 470.50.
    after = replay(canonical_ledger_rows)
    assert after.positions["VOO"].avg_cost_usd == Decimal("470.5000")
    assert after.positions["VOO"].cost_basis_usd == Decimal("705.7500")


def test_partial_sell_preserves_avg_cost_on_non_terminating_basis(
    txn_builder: Callable[..., Transaction],
) -> None:
    # 3 shares for a basis of 100.0000 -> avg 33.3333 (a basis that does NOT
    # divide evenly). A partial sell of 1 share must leave avg_cost EXACTLY
    # 33.3333 (DESIGN 7.1) — the rounding crumb flows into realized, not into
    # the retained basis. cost_basis -> 1.5dp-safe Q4(2 x 33.3333) = 66.6666.
    rows = [
        txn_builder(
            transaction_date=date(2026, 1, 1),
            transaction_type="DEPOSIT",
            total_amount_myr=Decimal("445.0000"),
            fx_rate_recorded=Decimal("4.4500"),
        ),
        txn_builder(
            transaction_date=date(2026, 1, 2),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("3.0000"),
            unit_price_usd=Decimal("33.333333333333"),
            fee_usd=Decimal("0.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("445.0000"),
        ),
    ]
    before = replay(rows)
    assert before.positions["VOO"].avg_cost_usd == Decimal("33.3333")

    rows.append(
        txn_builder(
            transaction_date=date(2026, 1, 3),
            transaction_type="SELL",
            asset_symbol="VOO",
            quantity=Decimal("1.0000"),
            unit_price_usd=Decimal("40.0000"),
            fee_usd=Decimal("0.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("178.0000"),
        )
    )
    after = replay(rows)
    voo = after.positions["VOO"]
    assert voo.quantity == Decimal("2.0000")
    # avg_cost is INVARIANT under the partial sell.
    assert voo.avg_cost_usd == Decimal("33.3333")
    assert voo.cost_basis_usd == Decimal("66.6666")
    # Realized = proceeds 40 - cost_removed (100 - 66.6666 = 33.3334) = 6.6666.
    assert after.realized_gain_usd == Decimal("6.6666")

    # Full liquidation of the remainder conserves money end-to-end: total
    # realized across both sells equals proceeds 40 + 100 - original basis 100.
    rows.append(
        txn_builder(
            transaction_date=date(2026, 1, 4),
            transaction_type="SELL",
            asset_symbol="VOO",
            quantity=Decimal("2.0000"),
            unit_price_usd=Decimal("50.0000"),
            fee_usd=Decimal("0.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("445.0000"),
        )
    )
    final = replay(rows)
    assert "VOO" not in final.positions
    assert final.realized_gain_usd == Decimal("40.0000")


def test_external_flows_investor_perspective(
    canonical_ledger_rows: list[Transaction],
) -> None:
    state = replay(canonical_ledger_rows)
    # Deposits are negative flows (money leaves the investor's pocket).
    assert state.external_flows == [
        (date(2026, 1, 5), Decimal("-2000.0000"), Decimal("-8900.0000")),
        (date(2026, 2, 3), Decimal("-1000.0000"), Decimal("-4450.0000")),
    ]


def test_as_of_replay_stops_at_cutoff(
    canonical_ledger_rows: list[Transaction],
) -> None:
    # Through 2026-02-28 only the two deposits and both buys apply:
    # cash 3000 - 941 - 401 = 1,658; no sell/dividend/fee yet.
    state = replay(canonical_ledger_rows, as_of=date(2026, 2, 28))
    assert state.cash_usd == Decimal("1658.0000")
    assert state.positions["VOO"].quantity == Decimal("2.0000")
    assert state.positions["QQQ"].quantity == Decimal("1.0000")
    assert state.realized_gain_usd == Decimal("0")
    assert state.dividends_usd == Decimal("0")
    assert state.fees_usd == Decimal("0")


def test_oversell_raises_naming_the_date(
    canonical_ledger_rows: list[Transaction],
    txn_builder: Callable[..., Transaction],
) -> None:
    # Only 1.5 VOO are held after 2026-03-17; selling 5 must fail loudly.
    oversell = txn_builder(
        transaction_date=date(2026, 4, 1),
        transaction_type="SELL",
        asset_symbol="VOO",
        quantity=Decimal("5.0000"),
        unit_price_usd=Decimal("500.0000"),
        fx_rate_recorded=Decimal("4.4500"),
        total_amount_myr=Decimal("11125.0000"),
    )
    with pytest.raises(ValidationFailed) as excinfo:
        validate_ledger([*canonical_ledger_rows, oversell])
    message = str(excinfo.value)
    assert "exceeds held quantity" in message
    assert "2026-04-01" in message
    assert "1.5000 VOO" in message


def test_negative_cash_is_warning_not_error(
    txn_builder: Callable[..., Transaction],
) -> None:
    # A buy without any deposit drives cash negative: data-quality warning.
    rows = [
        txn_builder(
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("1.0000"),
            unit_price_usd=Decimal("470.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("2091.5000"),
        )
    ]
    state = replay(rows)
    assert state.cash_usd == Decimal("-470.0000")
    assert len(state.warnings) == 1
    assert "negative" in state.warnings[0]
    assert "2026-01-10" in state.warnings[0]


def test_structural_validation_failures(
    txn_builder: Callable[..., Transaction],
) -> None:
    base = {
        "transaction_date": date(2026, 1, 10),
        "fx_rate_recorded": Decimal("4.4500"),
    }
    missing_symbol = txn_builder(
        transaction_type="BUY",
        quantity=Decimal("1.0000"),
        unit_price_usd=Decimal("100.0000"),
        total_amount_myr=Decimal("445.0000"),
        **base,
    )
    with pytest.raises(ValidationFailed, match="asset_symbol"):
        replay([missing_symbol])

    non_positive_amount = txn_builder(
        transaction_type="DEPOSIT",
        total_amount_myr=Decimal("0.0000"),
        **base,
    )
    with pytest.raises(ValidationFailed, match="positive total_amount_myr"):
        replay([non_positive_amount])

    bad_fx = txn_builder(
        transaction_type="DEPOSIT",
        transaction_date=date(2026, 1, 10),
        fx_rate_recorded=Decimal("0.0000"),
        total_amount_myr=Decimal("445.0000"),
    )
    with pytest.raises(ValidationFailed, match="positive fx_rate_recorded"):
        replay([bad_fx])

    unknown_type = txn_builder(
        transaction_type="INTEREST",
        total_amount_myr=Decimal("10.0000"),
        **base,
    )
    with pytest.raises(ValidationFailed, match="Unknown transaction type"):
        replay([unknown_type])

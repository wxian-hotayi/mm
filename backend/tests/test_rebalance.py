"""Rebalance engine: NO_ACTION, CASH_ONLY and SELL_REQUIRED with exact 4dp
share quantities, plus the /portfolio/rebalance endpoint over the canonical
seeded ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.core.errors import ValidationFailed
from app.models.ips import IpsRule
from app.models.transaction import Transaction
from app.services.ledger import LedgerState, Position, replay
from app.services.rebalance import RebalanceStatus, plan_rebalance
from conftest import API, SeededLedger


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _deposit(
    txn_builder: Callable[..., Transaction],
    on: date,
    myr: str,
    fx: str,
) -> Transaction:
    return txn_builder(
        transaction_date=on,
        transaction_type="DEPOSIT",
        fx_rate_recorded=Decimal(fx),
        total_amount_myr=Decimal(myr),
    )


def _buy(
    txn_builder: Callable[..., Transaction],
    on: date,
    symbol: str,
    quantity: str,
    price: str,
    fx: str,
) -> Transaction:
    quantity_dec = Decimal(quantity)
    price_dec = Decimal(price)
    return txn_builder(
        transaction_date=on,
        transaction_type="BUY",
        asset_symbol=symbol,
        quantity=quantity_dec,
        unit_price_usd=price_dec,
        fx_rate_recorded=Decimal(fx),
        total_amount_myr=quantity_dec * price_dec * Decimal(fx),
    )


def test_no_action_within_threshold(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # Deposit $1,000 (RM4,450 @ 4.45), buy 7 VOO + 3 QQQ at $100 each:
    # weights are exactly 70/30 with zero cash -> nothing to do.
    state = replay(
        [
            _deposit(txn_builder, date(2026, 1, 5), "4450.0000", "4.45"),
            _buy(txn_builder, date(2026, 1, 6), "VOO", "7", "100", "4.45"),
            _buy(txn_builder, date(2026, 1, 20), "QQQ", "3", "100", "4.45"),
        ]
    )
    plan = plan_rebalance(
        state,
        {"VOO": Decimal("100.0000"), "QQQ": Decimal("100.0000")},
        Decimal("4.4500"),
        ips_factory(),
    )
    assert plan.status is RebalanceStatus.NO_ACTION
    assert plan.orders == []
    assert plan.max_abs_drift_pp == Decimal("0.0000")
    assert plan.leftover_cash_usd == Decimal("0.0000")
    assert "discipline wins" in plan.message
    assert plan.steps == ["1. Do nothing. The allocation is within policy."]


def test_cash_only_exact_share_quantities(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # All-cash portfolio: $1,000 deployable, prices VOO $470 / QQQ $400.
    # Targets: VOO $700 -> 700/470 = 1.48936... floored to 1.4893 shares
    # (est 1.4893 x 470 = $699.9710); QQQ $300 -> exactly 0.7500 shares.
    # Leftover = 1000 - 699.9710 - 300 = $0.0290; nothing is ever sold.
    state = replay(
        [_deposit(txn_builder, date(2026, 1, 5), "4450.0000", "4.45")]
    )
    plan = plan_rebalance(
        state,
        {"VOO": Decimal("470.0000"), "QQQ": Decimal("400.0000")},
        Decimal("4.4500"),
        ips_factory(),
    )
    assert plan.status is RebalanceStatus.CASH_ONLY
    orders = {order.symbol: order for order in plan.orders}
    assert set(orders) == {"VOO", "QQQ"}
    assert all(order.side == "BUY" for order in plan.orders)

    assert orders["VOO"].quantity == Decimal("1.4893")
    assert orders["VOO"].est_amount_usd == Decimal("699.9710")
    # MYR estimate: 699.9710 x 4.45 = 3,114.87095 -> RM3,114.87 (2dp).
    assert orders["VOO"].est_amount_myr == Decimal("3114.87")
    assert orders["QQQ"].quantity == Decimal("0.7500")
    assert orders["QQQ"].est_amount_usd == Decimal("300.0000")
    assert orders["QQQ"].est_amount_myr == Decimal("1335.00")

    deployable = Decimal("1000")
    spent = sum(
        (order.est_amount_usd for order in plan.orders), start=Decimal("0")
    )
    assert spent <= deployable
    assert plan.leftover_cash_usd == Decimal("0.0290")
    assert plan.max_abs_drift_pp == Decimal("70.0000")
    assert plan.post_trade_weights_pct == {
        "VOO": Decimal("69.9971"),
        "QQQ": Decimal("30.0000"),
        "CASH": Decimal("0.0029"),
    }


def test_cash_only_with_planned_contribution(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # $500 idle cash + $500 planned contribution = the same $1,000
    # deployable as above; the contribution is deployed before any selling
    # is considered and buys can never exceed deployable cash.
    state = replay(
        [_deposit(txn_builder, date(2026, 1, 5), "2225.0000", "4.45")]
    )
    plan = plan_rebalance(
        state,
        {"VOO": Decimal("470.0000"), "QQQ": Decimal("400.0000")},
        Decimal("4.4500"),
        ips_factory(),
        extra_cash_usd=Decimal("500.0000"),
    )
    assert plan.status is RebalanceStatus.CASH_ONLY
    assert all(order.side == "BUY" for order in plan.orders)
    spent = sum(
        (order.est_amount_usd for order in plan.orders), start=Decimal("0")
    )
    assert spent <= Decimal("1000")
    assert plan.leftover_cash_usd == Decimal("0.0290")
    assert "idle cash first" in plan.priority_note


def test_sell_required_exact_quantities_and_post_trade_weights(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # $1,100 deposited (RM4,840 @ 4.40), all invested: 10 VOO + 1 QQQ at
    # $100 -> VOO weight 90.9091% vs 70% target (drift +20.9091pp).
    # Targets on $1,100: VOO $770 (delta -230 -> SELL 2.3000 sh),
    # QQQ $330 (delta +230 -> BUY 2.3000 sh). Post-trade: exactly 70/30
    # with zero leftover cash.
    state = replay(
        [
            _deposit(txn_builder, date(2026, 1, 5), "4840.0000", "4.40"),
            _buy(txn_builder, date(2026, 1, 6), "VOO", "10", "100", "4.40"),
            _buy(txn_builder, date(2026, 1, 20), "QQQ", "1", "100", "4.40"),
        ]
    )
    assert state.cash_usd == Decimal("0.0000")
    plan = plan_rebalance(
        state,
        {"VOO": Decimal("100.0000"), "QQQ": Decimal("100.0000")},
        Decimal("4.4000"),
        ips_factory(),
    )
    assert plan.status is RebalanceStatus.SELL_REQUIRED
    assert [(o.symbol, o.side) for o in plan.orders] == [
        ("VOO", "SELL"),
        ("QQQ", "BUY"),
    ]
    sell, buy = plan.orders
    assert sell.quantity == Decimal("2.3000")
    assert sell.est_amount_usd == Decimal("230.0000")
    assert buy.quantity == Decimal("2.3000")
    assert buy.est_amount_usd == Decimal("230.0000")
    assert plan.max_abs_drift_pp == Decimal("20.9091")
    assert plan.leftover_cash_usd == Decimal("0.0000")
    assert plan.post_trade_weights_pct == {
        "VOO": Decimal("70.0000"),
        "QQQ": Decimal("30.0000"),
        "CASH": Decimal("0.0000"),
    }
    # Sells are listed (and executed) before buys: cash funds the buys.
    assert plan.orders[0].side == "SELL"


def test_sell_required_never_overspends_deployable_cash(
    ips_factory: Callable[..., IpsRule],
) -> None:
    # Adversarial SELL_REQUIRED case (prices that don't divide evenly): BUYs
    # must be floored against the cash actually available after the sells, so
    # leftover cash is never negative — the plan can never overspend.
    state = LedgerState(cash_usd=Decimal("32923.9022"))
    state.positions["VOO"] = Position(
        symbol="VOO",
        quantity=Decimal("261.0000"),
        cost_basis_usd=Decimal("122265.7392"),
    )
    state.positions["QQQ"] = Position(
        symbol="QQQ",
        quantity=Decimal("1578.0000"),
        cost_basis_usd=Decimal("657991.4418"),
    )
    plan = plan_rebalance(
        state,
        {"VOO": Decimal("468.4472"), "QQQ": Decimal("416.9781")},
        Decimal("4.4500"),
        ips_factory(),
        extra_cash_usd=Decimal("9963.0000"),
    )
    assert plan.status is RebalanceStatus.SELL_REQUIRED
    assert plan.leftover_cash_usd >= Decimal("0")
    # Net deployment never exceeds deployable cash + sell proceeds.
    deployable = state.cash_usd + Decimal("9963.0000")
    net_spent = sum(
        (
            order.est_amount_usd
            if order.side == "BUY"
            else -order.est_amount_usd
            for order in plan.orders
        ),
        start=Decimal("0"),
    )
    assert net_spent <= deployable
    assert plan.leftover_cash_usd == deployable - net_spent
    assert plan.post_trade_weights_pct["CASH"] >= Decimal("0")


def test_negative_extra_cash_rejected(
    ips_factory: Callable[..., IpsRule],
) -> None:
    with pytest.raises(ValidationFailed, match="extra_cash_usd"):
        plan_rebalance(
            LedgerState(),
            {"VOO": Decimal("470.0000"), "QQQ": Decimal("400.0000")},
            Decimal("4.4500"),
            ips_factory(),
            extra_cash_usd=Decimal("-1.0000"),
        )


def test_missing_target_price_rejected(
    ips_factory: Callable[..., IpsRule],
) -> None:
    with pytest.raises(ValidationFailed, match="QQQ"):
        plan_rebalance(
            LedgerState(),
            {"VOO": Decimal("470.0000")},
            Decimal("4.4500"),
            ips_factory(),
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_rebalance_endpoint_on_canonical_ledger(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    # Canonical ledger priced at VOO $500 / QQQ $410, FX 4.50:
    # values VOO $750, QQQ $410, cash $1,909.20 -> investable $3,069.20.
    # Targets: VOO 2,148.44 (delta +1,398.44 -> floor 2.7968 sh, est
    # $1,398.40), QQQ 920.76 (delta +510.76 -> floor 1.2457 sh, est
    # $510.7370). Cash covers both -> CASH_ONLY, leftover
    # 1,909.20 - 1,398.40 - 510.737 = $0.0630.
    response = await client.post(
        f"{API}/portfolio/rebalance",
        json={"prices": {"VOO": "500", "QQQ": "410"}, "fx_rate": "4.50"},
        headers=seeded_ledger.user.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CASH_ONLY"
    orders = {order["symbol"]: order for order in body["orders"]}
    assert _dec(orders["VOO"]["quantity"]) == Decimal("2.7968")
    assert _dec(orders["VOO"]["est_amount_usd"]) == Decimal("1398.40")
    assert _dec(orders["QQQ"]["quantity"]) == Decimal("1.2457")
    assert _dec(orders["QQQ"]["est_amount_usd"]) == Decimal("510.737")
    assert _dec(body["leftover_cash_usd"]) == Decimal("0.063")
    assert _dec(body["max_drift_pp"]) == Decimal("45.5637")
    assert _dec(body["current_weights"]["VOO"]) == Decimal("24.4363")
    assert _dec(body["current_weights"]["QQQ"]) == Decimal("13.3585")
    assert _dec(body["current_weights"]["CASH"]) == Decimal("62.2051")
    assert _dec(body["post_trade_weights"]["VOO"]) == Decimal("69.9987")
    assert _dec(body["post_trade_weights"]["QQQ"]) == Decimal("29.9993")
    assert _dec(body["post_trade_weights"]["CASH"]) == Decimal("0.0021")
    assert len(body["steps"]) == 2
    assert "idle cash first" in body["priority_note"]

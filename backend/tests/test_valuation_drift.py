"""Valuation (NAV USD/MYR, weights) and IPS drift — exact Decimal numbers.

Canonical ledger priced at VOO $500 / QQQ $410, FX 4.50 (hand-computed):
  VOO: 1.5 sh -> $750.00; unrealized 750 - 705.75 = $44.25
       (44.25 x 100 / 705.75 = 6.2699%); weight 750 x 100 / 3069.2 = 24.4363%
  QQQ: 1 sh -> $410.00; unrealized 410 - 401 = $9.00
       (900 / 401 = 2.2444%); weight 410 x 100 / 3069.2 = 13.3585%
  NAV  = 750 + 410 + 1909.20 = $3,069.20; MYR 3069.20 x 4.50 = RM13,811.40
  cash weight = 1909.2 x 100 / 3069.2 = 62.2051%
  total P&L = 44.25 + 9 (unrealized) + 13.75 (realized) + 3.30 (dividends)
              - 1.10 (fees) = $69.20 -> 69.2 x 100 / 3000 = 2.3067%
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.models.ips import IpsRule
from app.models.transaction import Transaction
from app.services.drift import drift, parse_target_weights
from app.services.ledger import replay
from app.services.valuation import valuation
from conftest import API, SeededLedger


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


CANONICAL_PRICES = {"VOO": "500", "QQQ": "410"}
CANONICAL_FX = "4.50"


@pytest.mark.asyncio(loop_scope="session")
async def test_valuation_endpoint_exact_nav_and_weights(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    response = await client.post(
        f"{API}/portfolio/valuation",
        json={"prices": CANONICAL_PRICES, "fx_rate": CANONICAL_FX},
        headers=seeded_ledger.user.headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()

    assert _dec(body["nav_usd"]) == Decimal("3069.2")
    assert _dec(body["nav_myr"]) == Decimal("13811.40")
    assert _dec(body["cash_usd"]) == Decimal("1909.2")
    assert _dec(body["fx_rate"]) == Decimal("4.5")
    assert _dec(body["cash_weight_pct"]) == Decimal("62.2051")
    assert _dec(body["unrealized_usd"]) == Decimal("53.25")
    assert _dec(body["realized_usd"]) == Decimal("13.75")
    assert _dec(body["dividends_usd"]) == Decimal("3.3")
    assert _dec(body["fees_usd"]) == Decimal("1.1")
    assert _dec(body["total_pnl_usd"]) == Decimal("69.2")
    assert _dec(body["total_pnl_pct"]) == Decimal("2.3067")
    assert _dec(body["net_deposits_usd"]) == Decimal("3000")
    assert _dec(body["net_deposits_myr"]) == Decimal("13350")

    holdings = {item["symbol"]: item for item in body["holdings"]}
    assert set(holdings) == {"VOO", "QQQ"}
    voo = holdings["VOO"]
    assert _dec(voo["quantity"]) == Decimal("1.5")
    assert _dec(voo["avg_cost_usd"]) == Decimal("470.5")
    assert _dec(voo["cost_basis_usd"]) == Decimal("705.75")
    assert _dec(voo["market_value_usd"]) == Decimal("750")
    assert _dec(voo["unrealized_usd"]) == Decimal("44.25")
    assert _dec(voo["unrealized_pct"]) == Decimal("6.2699")
    assert _dec(voo["weight_pct"]) == Decimal("24.4363")
    qqq = holdings["QQQ"]
    assert _dec(qqq["quantity"]) == Decimal("1")
    assert _dec(qqq["avg_cost_usd"]) == Decimal("401")
    assert _dec(qqq["market_value_usd"]) == Decimal("410")
    assert _dec(qqq["unrealized_usd"]) == Decimal("9")
    assert _dec(qqq["unrealized_pct"]) == Decimal("2.2444")
    assert _dec(qqq["weight_pct"]) == Decimal("13.3585")


@pytest.mark.asyncio(loop_scope="session")
async def test_valuation_endpoint_as_of_cutoff(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    # As of 2026-02-28 the sell/dividend/fee have not happened: 2 VOO,
    # 1 QQQ, cash $1,658 -> NAV = 1000 + 410 + 1658 = $3,068 at the same
    # prices; MYR = 3068 x 4.50 = RM13,806.
    response = await client.post(
        f"{API}/portfolio/valuation",
        json={
            "prices": CANONICAL_PRICES,
            "fx_rate": CANONICAL_FX,
            "as_of": "2026-02-28",
        },
        headers=seeded_ledger.user.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert _dec(body["cash_usd"]) == Decimal("1658")
    assert _dec(body["nav_usd"]) == Decimal("3068")
    assert _dec(body["nav_myr"]) == Decimal("13806.00")
    assert _dec(body["realized_usd"]) == Decimal("0")
    assert _dec(body["dividends_usd"]) == Decimal("0")
    holdings = {item["symbol"]: item for item in body["holdings"]}
    assert _dec(holdings["VOO"]["quantity"]) == Decimal("2")
    assert _dec(holdings["VOO"]["cost_basis_usd"]) == Decimal("941")


@pytest.mark.asyncio(loop_scope="session")
async def test_valuation_missing_price_422_names_symbol(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    response = await client.post(
        f"{API}/portfolio/valuation",
        json={"prices": {"VOO": "500"}, "fx_rate": CANONICAL_FX},
        headers=seeded_ledger.user.headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert body["detail"] == "Missing USD price for held symbols: QQQ"


@pytest.mark.asyncio(loop_scope="session")
async def test_valuation_rejects_non_positive_inputs(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    # Pydantic rejects a non-positive FX rate before the service runs.
    zero_fx = await client.post(
        f"{API}/portfolio/valuation",
        json={"prices": CANONICAL_PRICES, "fx_rate": "0"},
        headers=seeded_ledger.user.headers,
    )
    assert zero_fx.status_code == 422
    negative_price = await client.post(
        f"{API}/portfolio/valuation",
        json={"prices": {"VOO": "-500", "QQQ": "410"}, "fx_rate": "4.50"},
        headers=seeded_ledger.user.headers,
    )
    assert negative_price.status_code == 422


# ---------------------------------------------------------------------------
# Pure drift math
# ---------------------------------------------------------------------------


def _skewed_state_rows(
    txn_builder: Callable[..., Transaction],
) -> list[Transaction]:
    # $1,100 fully invested: 10 VOO + 1 QQQ at $100 each.
    rows = [
        txn_builder(
            transaction_date=date(2026, 1, 5),
            transaction_type="DEPOSIT",
            fx_rate_recorded=Decimal("4.4000"),
            total_amount_myr=Decimal("4840.0000"),
        ),
        txn_builder(
            transaction_date=date(2026, 1, 6),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("10.0000"),
            unit_price_usd=Decimal("100.0000"),
            fx_rate_recorded=Decimal("4.4000"),
            total_amount_myr=Decimal("4400.0000"),
        ),
        txn_builder(
            transaction_date=date(2026, 1, 20),
            transaction_type="BUY",
            asset_symbol="QQQ",
            quantity=Decimal("1.0000"),
            unit_price_usd=Decimal("100.0000"),
            fx_rate_recorded=Decimal("4.4000"),
            total_amount_myr=Decimal("440.0000"),
        ),
    ]
    return rows


def test_drift_exact_numbers_when_skewed(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # Weights: VOO 1000/1100 = 90.9091%, QQQ 100/1100 = 9.0909%; targets
    # 70/30 -> drift +20.9091pp / -20.9091pp; cash 0% -> drag 0 - 5 = -5pp.
    state = replay(_skewed_state_rows(txn_builder))
    snapshot = valuation(
        state,
        {"VOO": Decimal("100.0000"), "QQQ": Decimal("100.0000")},
        Decimal("4.4000"),
    )
    report = drift(snapshot, ips_factory())
    items = {item.symbol: item for item in report.items}
    assert items["VOO"].weight_pct == Decimal("90.9091")
    assert items["VOO"].target_pct == Decimal("70.0000")
    assert items["VOO"].drift_pp == Decimal("20.9091")
    assert items["QQQ"].weight_pct == Decimal("9.0909")
    assert items["QQQ"].drift_pp == Decimal("-20.9091")
    assert report.max_abs_drift_pp == Decimal("20.9091")
    assert report.within_threshold is False
    assert report.cash_drag_pp == Decimal("-5.0000")


def test_drift_zero_at_exact_targets(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    rows = [
        txn_builder(
            transaction_date=date(2026, 1, 5),
            transaction_type="DEPOSIT",
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("4450.0000"),
        ),
        txn_builder(
            transaction_date=date(2026, 1, 6),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("7.0000"),
            unit_price_usd=Decimal("100.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("3115.0000"),
        ),
        txn_builder(
            transaction_date=date(2026, 1, 20),
            transaction_type="BUY",
            asset_symbol="QQQ",
            quantity=Decimal("3.0000"),
            unit_price_usd=Decimal("100.0000"),
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("1335.0000"),
        ),
    ]
    state = replay(rows)
    snapshot = valuation(
        state,
        {"VOO": Decimal("100.0000"), "QQQ": Decimal("100.0000")},
        Decimal("4.4500"),
    )
    report = drift(snapshot, ips_factory())
    assert report.max_abs_drift_pp == Decimal("0.0000")
    assert report.within_threshold is True
    assert all(item.drift_pp == Decimal("0.0000") for item in report.items)


def test_parse_target_weights_exact_decimals() -> None:
    weights = parse_target_weights('{"VOO": "0.70", "QQQ": "0.30"}')
    assert weights == {"VOO": Decimal("0.70"), "QQQ": Decimal("0.30")}
    # Numeric JSON values parse losslessly via Decimal as well.
    numeric = parse_target_weights('{"voo": 0.7, "qqq": 0.3}')
    assert numeric == {"VOO": Decimal("0.7"), "QQQ": Decimal("0.3")}

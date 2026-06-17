"""Behavior-protection engine: deterministic flags, audit recording and the
/analytics/behavior endpoint.

Pure tests pin ``today`` so window math is reproducible forever; endpoint
tests seed dates relative to the KL calendar "today".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.ips import IpsRule
from app.models.transaction import Transaction
from app.services.behavior import (
    ALLOCATION_DRIFT,
    CONTRIBUTION_GAP,
    EARLY_SELL,
    EXCESSIVE_CASH,
    FORBIDDEN_ASSET,
    HIGH_FREQUENCY_TRADING,
    BehaviorFlag,
    compute_flags,
)
from app.utils.dates import kl_today
from conftest import API, SeededLedger, UserFactory

TODAY = date(2026, 6, 1)


def _deposit(
    txn_builder: Callable[..., Transaction], on: date, myr: str, fx: str
) -> Transaction:
    return txn_builder(
        transaction_date=on,
        transaction_type="DEPOSIT",
        fx_rate_recorded=Decimal(fx),
        total_amount_myr=Decimal(myr),
    )


def _trade(
    txn_builder: Callable[..., Transaction],
    on: date,
    side: str,
    symbol: str,
    quantity: str,
    price: str,
) -> Transaction:
    quantity_dec = Decimal(quantity)
    price_dec = Decimal(price)
    return txn_builder(
        transaction_date=on,
        transaction_type=side,
        asset_symbol=symbol,
        quantity=quantity_dec,
        unit_price_usd=price_dec,
        fx_rate_recorded=Decimal("4.4500"),
        total_amount_myr=quantity_dec * price_dec * Decimal("4.4500"),
    )


def _codes(flags: list[BehaviorFlag]) -> set[str]:
    return {flag.code for flag in flags}


# ---------------------------------------------------------------------------
# Pure flag computation (fixed `today`)
# ---------------------------------------------------------------------------


def test_high_frequency_trading_three_trades_in_seven_days(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    rows = [
        _deposit(txn_builder, date(2026, 5, 20), "44500.0000", "4.45"),
        _trade(txn_builder, date(2026, 5, 25), "BUY", "VOO", "1", "100"),
        _trade(txn_builder, date(2026, 5, 27), "BUY", "VOO", "1", "100"),
        _trade(txn_builder, date(2026, 5, 29), "SELL", "VOO", "1", "110"),
    ]
    flags = compute_flags(rows, ips_factory(), today=TODAY)
    hft = [flag for flag in flags if flag.code == HIGH_FREQUENCY_TRADING]
    assert len(hft) == 1
    assert hft[0].severity == AuditSeverity.WARNING.value
    assert hft[0].evidence["trade_dates"] == [
        "2026-05-25",
        "2026-05-27",
        "2026-05-29",
    ]


def test_no_hft_flag_when_trades_are_spaced_out(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    rows = [
        _deposit(txn_builder, date(2026, 5, 1), "44500.0000", "4.45"),
        _trade(txn_builder, date(2026, 5, 5), "BUY", "VOO", "1", "100"),
        _trade(txn_builder, date(2026, 5, 14), "BUY", "VOO", "1", "100"),
        _trade(txn_builder, date(2026, 5, 23), "BUY", "QQQ", "1", "100"),
    ]
    flags = compute_flags(rows, ips_factory(), today=TODAY)
    assert HIGH_FREQUENCY_TRADING not in _codes(flags)


def test_forbidden_asset_on_tsla_buy(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    rows = [
        _deposit(txn_builder, date(2026, 5, 20), "4450.0000", "4.45"),
        _trade(txn_builder, date(2026, 5, 21), "BUY", "TSLA", "1", "200"),
    ]
    flags = compute_flags(rows, ips_factory(), today=TODAY)
    forbidden = [flag for flag in flags if flag.code == FORBIDDEN_ASSET]
    assert len(forbidden) == 1
    assert forbidden[0].severity == AuditSeverity.CRITICAL.value
    assert forbidden[0].evidence["forbidden_symbols"] == ["TSLA"]
    assert "TSLA" in forbidden[0].message


def test_early_sell_before_min_holding_period(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    rows = [
        _deposit(txn_builder, date(2025, 1, 5), "4450.0000", "4.45"),
        _trade(txn_builder, date(2025, 1, 10), "BUY", "VOO", "2", "100"),
        _trade(txn_builder, date(2026, 2, 10), "SELL", "VOO", "0.5", "110"),
        _deposit(txn_builder, date(2026, 5, 20), "445.0000", "4.45"),
    ]
    flags = compute_flags(rows, ips_factory(), today=TODAY)
    early = [flag for flag in flags if flag.code == EARLY_SELL]
    assert len(early) == 1
    events = early[0].evidence["events"]
    assert events == [
        {
            "symbol": "VOO",
            "sell_date": "2026-02-10",
            "first_buy_date": "2025-01-10",
            # 10-year minimum holding period from the first buy.
            "holding_period_ends": "2035-01-10",
        }
    ]


def test_contribution_gap_after_45_days(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # Last deposit 2026-04-01, today 2026-06-01 -> 61-day gap (INFO).
    rows = [_deposit(txn_builder, date(2026, 4, 1), "4450.0000", "4.45")]
    flags = compute_flags(rows, ips_factory(), today=TODAY)
    gap = [flag for flag in flags if flag.code == CONTRIBUTION_GAP]
    assert len(gap) == 1
    assert gap[0].severity == AuditSeverity.INFO.value
    assert gap[0].evidence["gap_days"] == 61
    assert gap[0].evidence["last_deposit_date"] == "2026-04-01"

    # A deposit inside the window clears the flag.
    recent = [_deposit(txn_builder, date(2026, 5, 20), "4450.0000", "4.45")]
    assert CONTRIBUTION_GAP not in _codes(
        compute_flags(recent, ips_factory(), today=TODAY)
    )


def test_drift_and_cash_flags_require_pricing(
    txn_builder: Callable[..., Transaction],
    ips_factory: Callable[..., IpsRule],
) -> None:
    # An all-cash portfolio is 100% cash and -70pp/-30pp from targets,
    # but those flags only fire when pricing context is supplied.
    rows = [_deposit(txn_builder, date(2026, 5, 25), "4450.0000", "4.45")]
    unpriced = compute_flags(rows, ips_factory(), today=TODAY)
    assert ALLOCATION_DRIFT not in _codes(unpriced)
    assert EXCESSIVE_CASH not in _codes(unpriced)

    priced = compute_flags(
        rows,
        ips_factory(),
        prices={},
        fx_rate=Decimal("4.4500"),
        today=TODAY,
    )
    codes = _codes(priced)
    assert ALLOCATION_DRIFT in codes
    assert EXCESSIVE_CASH in codes


def test_empty_ledger_has_no_flags(
    ips_factory: Callable[..., IpsRule],
) -> None:
    assert compute_flags([], ips_factory(), today=TODAY) == []


# ---------------------------------------------------------------------------
# Endpoint + audit recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_behavior_endpoint_hft_and_priced_flags(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    from app.db.session import SessionLocal
    from conftest import make_transaction

    user = await user_factory()
    today = kl_today()
    rows = [
        make_transaction(
            user_id=user.id,
            transaction_date=today - timedelta(days=10),
            transaction_type="DEPOSIT",
            fx_rate_recorded=Decimal("4.4500"),
            total_amount_myr=Decimal("44500.0000"),
        )
    ]
    for days_ago in (6, 4, 2):
        rows.append(
            make_transaction(
                user_id=user.id,
                transaction_date=today - timedelta(days=days_ago),
                transaction_type="BUY",
                asset_symbol="VOO",
                quantity=Decimal("1.0000"),
                unit_price_usd=Decimal("100.0000"),
                fx_rate_recorded=Decimal("4.4500"),
                total_amount_myr=Decimal("445.0000"),
            )
        )
    async with SessionLocal() as db:
        db.add_all(rows)
        await db.commit()

    unpriced = await client.get(
        f"{API}/analytics/behavior", headers=user.headers
    )
    assert unpriced.status_code == 200, unpriced.text
    body = unpriced.json()
    codes = {flag["code"] for flag in body["flags"]}
    assert HIGH_FREQUENCY_TRADING in codes
    assert ALLOCATION_DRIFT not in codes
    assert body["trade_stats"] == {
        "trades_30d": 3,
        "buys_30d": 3,
        "sells_30d": 0,
        "max_trades_in_7d": 3,
    }

    # With pricing: $10,000 deposited, $300 in VOO -> 97% cash, -67pp drift.
    priced = await client.get(
        f"{API}/analytics/behavior",
        params={"fx_rate": "4.45", "voo_price": "100"},
        headers=user.headers,
    )
    assert priced.status_code == 200, priced.text
    priced_codes = {flag["code"] for flag in priced.json()["flags"]}
    assert {
        HIGH_FREQUENCY_TRADING,
        ALLOCATION_DRIFT,
        EXCESSIVE_CASH,
    } <= priced_codes


@pytest.mark.asyncio(loop_scope="session")
async def test_behavior_endpoint_on_canonical_ledger(
    client: AsyncClient, seeded_ledger: SeededLedger
) -> None:
    # The canonical SELL (2026-03-17) is far inside the 10-year holding
    # period of the first VOO buy (2026-02-10); the last deposit
    # (2026-02-03) is more than 45 days ago.
    response = await client.get(
        f"{API}/analytics/behavior", headers=seeded_ledger.user.headers
    )
    assert response.status_code == 200, response.text
    codes = {flag["code"] for flag in response.json()["flags"]}
    assert EARLY_SELL in codes
    assert CONTRIBUTION_GAP in codes
    assert HIGH_FREQUENCY_TRADING not in codes
    assert FORBIDDEN_ASSET not in codes


async def _flag_row_count(user_id: int, action: str) -> int:
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        result = await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.event_type == AuditEventType.BEHAVIOR_FLAG.value,
                AuditLog.action == action,
            )
        )
        return result.scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_rows_recorded_with_same_day_dedupe(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    from app.db.session import SessionLocal

    user = await user_factory()
    today = kl_today()
    old_deposit = {
        "transaction_type": "DEPOSIT",
        "transaction_date": (today - timedelta(days=60)).isoformat(),
        "amount_usd": "1000",
        "fx_rate_recorded": "4.45",
    }
    first = await client.post(
        f"{API}/transactions", json=old_deposit, headers=user.headers
    )
    assert first.status_code == 201, first.text
    assert any(
        "Contribution gap" in warning
        for warning in first.json()["behavior_warnings"]
    )
    assert await _flag_row_count(user.id, CONTRIBUTION_GAP) == 1

    # The recorded row carries the flag's severity and message.
    async with SessionLocal() as db:
        result = await db.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.event_type == AuditEventType.BEHAVIOR_FLAG.value,
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].action == CONTRIBUTION_GAP
    assert rows[0].severity == AuditSeverity.INFO.value
    assert rows[0].description

    # A second mutation on the same KL day re-raises the warning but the
    # audit row is deduplicated per (code, day).
    fee = {
        "transaction_type": "FEE",
        "transaction_date": (today - timedelta(days=55)).isoformat(),
        "amount_usd": "1",
        "fx_rate_recorded": "4.45",
    }
    second = await client.post(
        f"{API}/transactions", json=fee, headers=user.headers
    )
    assert second.status_code == 201, second.text
    assert any(
        "Contribution gap" in warning
        for warning in second.json()["behavior_warnings"]
    )
    assert await _flag_row_count(user.id, CONTRIBUTION_GAP) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_forbidden_asset_flag_via_api_create(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    today = kl_today()
    deposit = {
        "transaction_type": "DEPOSIT",
        "transaction_date": (today - timedelta(days=30)).isoformat(),
        "amount_usd": "1000",
        "fx_rate_recorded": "4.45",
    }
    created = await client.post(
        f"{API}/transactions", json=deposit, headers=user.headers
    )
    assert created.status_code == 201, created.text

    tsla_buy = {
        "transaction_type": "BUY",
        "transaction_date": (today - timedelta(days=20)).isoformat(),
        "asset_symbol": "TSLA",
        "quantity": "1",
        "unit_price_usd": "200",
        "fx_rate_recorded": "4.45",
    }
    # Phase 2 (DESIGN §19.5, Decision Log 20): a forbidden-asset BUY is now
    # BLOCK-enforced — without an override the IPS enforcement gate rejects it
    # with HTTP 422 (it no longer succeeds advisory-only).
    blocked = await client.post(
        f"{API}/transactions", json=tsla_buy, headers=user.headers
    )
    assert blocked.status_code == 422, blocked.text
    assert "IPS enforcement" in blocked.json()["detail"]

    # The audited override is the sole bypass; the BUY then persists and the
    # FORBIDDEN_ASSET behavior flag is still computed and recorded.
    response = await client.post(
        f"{API}/transactions?override=true", json=tsla_buy, headers=user.headers
    )
    assert response.status_code == 201, response.text
    assert any(
        "Forbidden asset" in warning
        for warning in response.json()["behavior_warnings"]
    )
    assert await _flag_row_count(user.id, FORBIDDEN_ASSET) == 1

"""Transaction CRUD: validation, filters, sorting, pagination, isolation.

Every test uses its own isolated user (fresh empty ledger) so assertions are
deterministic regardless of execution order. Dates are derived from the KL
calendar "today" so the suite never trips the future-date rule.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.utils.dates import kl_today
from conftest import API, UserFactory

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _day(days_ago: int) -> str:
    return (kl_today() - timedelta(days=days_ago)).isoformat()


async def _create(
    client: AsyncClient,
    headers: dict[str, str],
    payload: dict[str, Any],
    expected_status: int = 201,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/transactions", json=payload, headers=headers
    )
    assert response.status_code == expected_status, response.text
    body: dict[str, Any] = response.json()
    return body


def _deposit(days_ago: int, amount_usd: str, notes: str = "") -> dict[str, Any]:
    return {
        "transaction_type": "DEPOSIT",
        "transaction_date": _day(days_ago),
        "amount_usd": amount_usd,
        "fx_rate_recorded": "4.45",
        "notes": notes,
    }


def _buy(
    days_ago: int, symbol: str, quantity: str, price: str, fee: str = "0"
) -> dict[str, Any]:
    return {
        "transaction_type": "BUY",
        "transaction_date": _day(days_ago),
        "asset_symbol": symbol,
        "quantity": quantity,
        "unit_price_usd": price,
        "fee_usd": fee,
        "fx_rate_recorded": "4.45",
    }


def _sell(
    days_ago: int, symbol: str, quantity: str, price: str, fee: str = "0"
) -> dict[str, Any]:
    return {
        "transaction_type": "SELL",
        "transaction_date": _day(days_ago),
        "asset_symbol": symbol,
        "quantity": quantity,
        "unit_price_usd": price,
        "fee_usd": fee,
        "fx_rate_recorded": "4.45",
    }


# ---------------------------------------------------------------------------
# Creation: every type, with server-derived amounts
# ---------------------------------------------------------------------------


async def test_create_each_type(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    headers = user.headers

    deposit = await _create(client, headers, _deposit(120, "2000"))
    # 2,000 USD x 4.45 = RM8,900 (MYR derived from amount_usd).
    assert _dec(deposit["total_amount_myr"]) == Decimal("8900")
    assert _dec(deposit["amount_usd"]) == Decimal("2000")
    assert isinstance(deposit["behavior_warnings"], list)

    buy = await _create(client, headers, _buy(100, "voo", "2", "470", "1"))
    # Trade MYR is server-derived: (2 x 470 + 1) x 4.45 = RM4,187.45.
    assert buy["asset_symbol"] == "VOO"
    assert _dec(buy["total_amount_myr"]) == Decimal("4187.45")
    assert _dec(buy["amount_usd"]) == Decimal("941")

    sell = await _create(client, headers, _sell(90, "VOO", "0.5", "500", "1"))
    # SELL cash impact: 0.5 x 500 - 1 = $249.
    assert _dec(sell["amount_usd"]) == Decimal("249")
    assert _dec(sell["total_amount_myr"]) == Decimal("1116.95")

    dividend = await _create(
        client,
        headers,
        {
            "transaction_type": "DIVIDEND",
            "transaction_date": _day(80),
            "asset_symbol": "VOO",
            "amount_usd": "3.30",
            "fx_rate_recorded": "4.45",
        },
    )
    assert _dec(dividend["total_amount_myr"]) == Decimal("14.685")
    assert _dec(dividend["amount_usd"]) == Decimal("3.3")

    fee = await _create(
        client,
        headers,
        {
            "transaction_type": "FEE",
            "transaction_date": _day(70),
            "amount_usd": "1.10",
            "fx_rate_recorded": "4.45",
        },
    )
    assert _dec(fee["total_amount_myr"]) == Decimal("4.895")

    withdrawal = await _create(
        client,
        headers,
        {
            "transaction_type": "WITHDRAWAL",
            "transaction_date": _day(60),
            "total_amount_myr": "445",
            "fx_rate_recorded": "4.45",
        },
    )
    # MYR is authoritative for cash events: 445 / 4.45 = $100.
    assert _dec(withdrawal["amount_usd"]) == Decimal("100")

    listing = await client.get(f"{API}/transactions", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 6


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


async def test_buy_without_symbol_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = _buy(10, "VOO", "1", "100")
    del payload["asset_symbol"]
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "BUY requires asset_symbol" in response.text


async def test_oversized_notes_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    # notes is bounded (max 2000 chars) to prevent per-user storage bloat and
    # oversized audit-log context payloads.
    user = await user_factory()
    payload = _deposit(10, "100", notes="x" * 2001)
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    # 2000 chars exactly is accepted.
    ok = _deposit(11, "100", notes="x" * 2000)
    accepted = await client.post(
        f"{API}/transactions", json=ok, headers=user.headers
    )
    assert accepted.status_code == 201, accepted.text


async def test_oversized_asset_symbol_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    # asset_symbol is bounded (max 16 chars).
    user = await user_factory()
    payload = _buy(10, "A" * 17, "1", "100")
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "asset_symbol" in response.text


async def test_sell_exceeding_holdings_names_date(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    await _create(client, user.headers, _deposit(40, "1000"))
    await _create(client, user.headers, _buy(35, "VOO", "1", "100"))
    oversell_date = _day(30)
    response = await client.post(
        f"{API}/transactions",
        json=_sell(30, "VOO", "2", "110"),
        headers=user.headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert "exceeds held quantity" in body["detail"]
    assert oversell_date in body["detail"]
    # Nothing was persisted by the rejected create.
    listing = await client.get(f"{API}/transactions", headers=user.headers)
    assert listing.json()["total"] == 2


async def test_future_date_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    tomorrow = (kl_today() + timedelta(days=1)).isoformat()
    payload = _deposit(0, "100")
    payload["transaction_date"] = tomorrow
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "future" in response.text


async def test_negative_fx_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = _deposit(10, "100")
    payload["fx_rate_recorded"] = "-4.45"
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422


async def test_trade_with_client_amount_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = _buy(10, "VOO", "1", "100")
    payload["total_amount_myr"] = "445"
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "derived server-side" in response.text


async def test_cash_event_with_both_amounts_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = _deposit(10, "100")
    payload["total_amount_myr"] = "445"
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "exactly one" in response.text


# ---------------------------------------------------------------------------
# Listing: filters, sorting, pagination
# ---------------------------------------------------------------------------


async def _seed_listing_ledger(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await _create(client, headers, _deposit(40, "2000", notes="salary top-up"))
    await _create(client, headers, _buy(35, "VOO", "2", "470", "1"))
    await _create(client, headers, _buy(30, "QQQ", "1", "400", "1"))
    await _create(client, headers, _sell(25, "VOO", "1", "500", "1"))
    await _create(
        client,
        headers,
        {
            "transaction_type": "DIVIDEND",
            "transaction_date": _day(20),
            "asset_symbol": "VOO",
            "amount_usd": "3.30",
            "fx_rate_recorded": "4.45",
        },
    )
    await _create(
        client,
        headers,
        {
            "transaction_type": "FEE",
            "transaction_date": _day(15),
            "amount_usd": "1.10",
            "fx_rate_recorded": "4.45",
        },
    )


async def test_list_filters_sort_pagination(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    headers = user.headers
    await _seed_listing_ledger(client, headers)

    full = (await client.get(f"{API}/transactions", headers=headers)).json()
    assert full["total"] == 6
    # Default sort: transaction_date descending -> newest (FEE) first.
    assert full["items"][0]["transaction_type"] == "FEE"
    assert full["items"][0]["transaction_date"] == _day(15)

    by_type = (
        await client.get(
            f"{API}/transactions", params={"type": "BUY"}, headers=headers
        )
    ).json()
    assert by_type["total"] == 2
    assert {item["transaction_type"] for item in by_type["items"]} == {"BUY"}

    by_symbol = (
        await client.get(
            f"{API}/transactions", params={"symbol": "voo"}, headers=headers
        )
    ).json()
    # BUY + SELL + DIVIDEND on VOO; symbol filter is case-insensitive.
    assert by_symbol["total"] == 3

    by_range = (
        await client.get(
            f"{API}/transactions",
            params={"date_from": _day(30), "date_to": _day(20)},
            headers=headers,
        )
    ).json()
    assert by_range["total"] == 3

    by_search = (
        await client.get(
            f"{API}/transactions", params={"search": "salary"}, headers=headers
        )
    ).json()
    assert by_search["total"] == 1
    assert by_search["items"][0]["transaction_type"] == "DEPOSIT"

    by_amount = (
        await client.get(
            f"{API}/transactions",
            params={"sort": "total_amount_myr", "order": "desc"},
            headers=headers,
        )
    ).json()
    # Largest MYR amount is the RM8,900 deposit.
    assert by_amount["items"][0]["transaction_type"] == "DEPOSIT"
    amounts = [_dec(item["total_amount_myr"]) for item in by_amount["items"]]
    assert amounts == sorted(amounts, reverse=True)

    page = (
        await client.get(
            f"{API}/transactions",
            params={
                "sort": "transaction_date",
                "order": "asc",
                "page": 2,
                "page_size": 2,
            },
            headers=headers,
        )
    ).json()
    assert page["total"] == 6
    assert page["page"] == 2
    assert page["page_size"] == 2
    assert len(page["items"]) == 2
    # Ascending date order: page 2 holds the 3rd and 4th oldest rows.
    assert page["items"][0]["transaction_date"] == _day(30)
    assert page["items"][1]["transaction_date"] == _day(25)


# ---------------------------------------------------------------------------
# Update / delete with full-ledger re-validation
# ---------------------------------------------------------------------------


async def test_patch_updates_notes(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    deposit = await _create(client, user.headers, _deposit(20, "1000"))
    response = await client.patch(
        f"{API}/transactions/{deposit['id']}",
        json={"notes": "updated note"},
        headers=user.headers,
    )
    assert response.status_code == 200
    assert response.json()["notes"] == "updated note"
    fetched = await client.get(
        f"{API}/transactions/{deposit['id']}", headers=user.headers
    )
    assert fetched.json()["notes"] == "updated note"


async def test_patch_revalidates_full_ledger(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    await _create(client, user.headers, _deposit(40, "1000"))
    buy = await _create(client, user.headers, _buy(35, "VOO", "1", "100"))
    sell_date = _day(30)
    await _create(client, user.headers, _sell(30, "VOO", "1", "110"))

    # Shrinking the BUY would make the later SELL an oversell.
    response = await client.patch(
        f"{API}/transactions/{buy['id']}",
        json={"quantity": "0.5"},
        headers=user.headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert "exceeds held quantity" in body["detail"]
    assert sell_date in body["detail"]

    # The rejected patch must not have been persisted.
    fetched = await client.get(
        f"{API}/transactions/{buy['id']}", headers=user.headers
    )
    assert _dec(fetched.json()["quantity"]) == Decimal("1")


async def test_patch_requires_at_least_one_field(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    deposit = await _create(client, user.headers, _deposit(20, "1000"))
    response = await client.patch(
        f"{API}/transactions/{deposit['id']}",
        json={},
        headers=user.headers,
    )
    assert response.status_code == 422


async def test_delete_revalidates_full_ledger(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    await _create(client, user.headers, _deposit(40, "1000"))
    buy = await _create(client, user.headers, _buy(35, "VOO", "1", "100"))
    sell = await _create(client, user.headers, _sell(30, "VOO", "1", "110"))

    # Removing the BUY would orphan the SELL -> rejected, row kept.
    response = await client.delete(
        f"{API}/transactions/{buy['id']}", headers=user.headers
    )
    assert response.status_code == 422
    assert "exceeds held quantity" in response.json()["detail"]
    still_there = await client.get(
        f"{API}/transactions/{buy['id']}", headers=user.headers
    )
    assert still_there.status_code == 200

    # Deleting the SELL itself keeps the ledger consistent.
    response = await client.delete(
        f"{API}/transactions/{sell['id']}", headers=user.headers
    )
    assert response.status_code == 204
    gone = await client.get(
        f"{API}/transactions/{sell['id']}", headers=user.headers
    )
    assert gone.status_code == 404
    listing = await client.get(f"{API}/transactions", headers=user.headers)
    assert listing.json()["total"] == 2


# ---------------------------------------------------------------------------
# Per-user isolation
# ---------------------------------------------------------------------------


async def test_user_isolation_404_for_foreign_rows(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    owner = await user_factory()
    intruder = await user_factory()
    deposit = await _create(client, owner.headers, _deposit(20, "1000"))
    txn_id = deposit["id"]

    fetched = await client.get(
        f"{API}/transactions/{txn_id}", headers=intruder.headers
    )
    assert fetched.status_code == 404
    patched = await client.patch(
        f"{API}/transactions/{txn_id}",
        json={"notes": "hijack"},
        headers=intruder.headers,
    )
    assert patched.status_code == 404
    deleted = await client.delete(
        f"{API}/transactions/{txn_id}", headers=intruder.headers
    )
    assert deleted.status_code == 404

    # The foreign ledger never leaks into the intruder's listing.
    listing = await client.get(f"{API}/transactions", headers=intruder.headers)
    assert listing.json()["total"] == 0
    # And the owner's row is untouched.
    owner_view = await client.get(
        f"{API}/transactions/{txn_id}", headers=owner.headers
    )
    assert owner_view.status_code == 200

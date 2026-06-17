"""Three-tier IPS Enforcement Engine (DESIGN §19.5, Decision Log 20).

Three tiers: INFO (record only), WARN (surfaces, allows execution), BLOCK
(rejects 422 — reserved for forbidden asset classes: leverage/options/non-allowed
instruments). Behavioral rules (drift/min-holding/cash-drag) are clamped to at
most WARN. Every BLOCK and override is audited as a critical IPS_ALERT.

These tests exercise: BLOCK verdicts for forbidden/leverage/options BUYs (and the
HTTP 422 enforcement point on transaction-create); WARN/INFO allow with warnings;
a behavioral rule set to BLOCK is clamped to WARN; the audited override path; and
the three-tier compliance-score arithmetic (hand-computed). Each test uses its
own isolated user.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.errors import ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.ips import IpsEnforcementLevel, IpsRule
from app.services import ips_enforcement
from app.utils.dates import kl_today
from conftest import API, UserFactory, UserLoader
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

_FX = Decimal("4.45")
_TODAY = kl_today().isoformat()


def _buy_action(symbol: str) -> dict[str, Any]:
    return {
        "kind": ips_enforcement.ACTION_TRANSACTION,
        "type": "BUY",
        "asset_symbol": symbol,
        "date": _TODAY,
    }


# --------------------------------------------------------------------------- #
# Deterministic symbol classification                                          #
# --------------------------------------------------------------------------- #
async def test_symbol_classifiers() -> None:
    assert ips_enforcement.is_option_symbol("AAPL240119C00150000") is True
    assert ips_enforcement.is_option_symbol("VOO CALL") is True
    assert ips_enforcement.is_option_symbol("VOO") is False
    assert ips_enforcement.is_leverage_symbol("TQQQ") is False  # no marker
    assert ips_enforcement.is_leverage_symbol("SPXL-3X") is True
    assert ips_enforcement.is_leverage_symbol("BULL") is True
    assert ips_enforcement.is_plain_equity_symbol("TSLA") is True
    assert ips_enforcement.is_plain_equity_symbol("VOO CALL") is False


# --------------------------------------------------------------------------- #
# BLOCK — forbidden / leverage / options BUY                                   #
# --------------------------------------------------------------------------- #
async def test_block_forbidden_individual_stock(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # TSLA is a plain equity outside the allowed VOO/QQQ list -> forbidden
    # individual stock at BLOCK level -> not allowed (no override).
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("TSLA")
    )
    assert verdict.allowed is False
    assert verdict.max_level == IpsEnforcementLevel.BLOCK.value
    assert len(verdict.violations) == 1
    assert verdict.violations[0].rule_type == ips_enforcement.RULE_FORBIDDEN_ASSET
    assert verdict.warnings == []


async def test_block_options(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("AAPL240119C00150000")
    )
    assert verdict.allowed is False
    assert verdict.violations[0].rule_type == ips_enforcement.RULE_OPTIONS


async def test_block_leverage(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("SPXL-3X")
    )
    assert verdict.allowed is False
    assert verdict.violations[0].rule_type == ips_enforcement.RULE_LEVERAGE


async def test_allowed_symbol_clean(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # VOO is allowed -> clean verdict (allowed, no violations/warnings).
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("VOO")
    )
    assert verdict.allowed is True
    assert verdict.max_level is None
    assert verdict.violations == []
    assert verdict.warnings == []


async def test_cash_events_never_restricted(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # A DEPOSIT carries no symbol and is never IPS-restricted -> clean.
    verdict = await ips_enforcement.validate_action(
        db_session,
        user,
        {"kind": ips_enforcement.ACTION_TRANSACTION, "type": "DEPOSIT"},
    )
    assert verdict.allowed is True
    assert verdict.max_level is None


# --------------------------------------------------------------------------- #
# HTTP 422 enforcement point on transaction-create                            #
# --------------------------------------------------------------------------- #
async def test_create_forbidden_buy_returns_422(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = {
        "transaction_type": "BUY",
        "transaction_date": _TODAY,
        "asset_symbol": "TSLA",
        "quantity": "1",
        "unit_price_usd": "100",
        "fee_usd": "0",
        "fx_rate_recorded": "4.45",
    }
    response = await client.post(
        f"{API}/transactions", json=payload, headers=user.headers
    )
    assert response.status_code == 422
    assert "IPS enforcement" in response.json()["detail"]
    # Nothing was persisted by the blocked create.
    listing = await client.get(f"{API}/transactions", headers=user.headers)
    assert listing.json()["total"] == 0


async def test_create_forbidden_buy_override_allows(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    user = await user_factory()
    payload = {
        "transaction_type": "BUY",
        "transaction_date": _TODAY,
        "asset_symbol": "TSLA",
        "quantity": "1",
        "unit_price_usd": "100",
        "fee_usd": "0",
        "fx_rate_recorded": "4.45",
    }
    # First a DEPOSIT so the BUY has cash (cash event is never IPS-restricted).
    await client.post(
        f"{API}/transactions",
        json={
            "transaction_type": "DEPOSIT",
            "transaction_date": _TODAY,
            "amount_usd": "1000",
            "fx_rate_recorded": "4.45",
        },
        headers=user.headers,
    )
    response = await client.post(
        f"{API}/transactions?override=true", json=payload, headers=user.headers
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------- #
# WARN / INFO allow execution with warnings (min-holding on SELL)              #
# --------------------------------------------------------------------------- #
async def test_min_holding_sell_is_warn_not_block(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # A VOO buy two months ago, then a SELL well within the 10-year minimum
    # holding period -> a WARN violation (allows execution, surfaces a warning).
    from app.models.transaction import Transaction

    db_session.add(
        Transaction(
            user_id=user.id,
            transaction_date=date(2026, 1, 6),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("2.0000"),
            unit_price_usd=Decimal("100.0000"),
            fee_usd=Decimal("0"),
            fx_rate_recorded=_FX,
            total_amount_myr=Decimal("890.0000"),
            notes="",
        )
    )
    await db_session.commit()
    verdict = await ips_enforcement.validate_action(
        db_session,
        user,
        {
            "kind": ips_enforcement.ACTION_TRANSACTION,
            "type": "SELL",
            "asset_symbol": "VOO",
            "date": "2026-03-01",
        },
    )
    # Allowed (WARN never blocks); the violation surfaces as a warning.
    assert verdict.allowed is True
    assert verdict.max_level == IpsEnforcementLevel.WARN.value
    assert verdict.violations == []
    assert len(verdict.warnings) == 1
    assert verdict.warnings[0].rule_type == ips_enforcement.RULE_MIN_HOLDING
    assert verdict.warnings[0].level == IpsEnforcementLevel.WARN.value


# --------------------------------------------------------------------------- #
# Behavioral rule set to BLOCK is clamped to WARN                              #
# --------------------------------------------------------------------------- #
async def test_behavioral_block_is_clamped_to_warn(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Operator misconfigures min-holding enforcement to BLOCK; the engine must
    # clamp it to WARN so policy can never hard-block the user's own ledger.
    result = await db_session.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one()
    ips.enforce_min_holding = IpsEnforcementLevel.BLOCK.value
    await db_session.commit()

    from app.models.transaction import Transaction

    db_session.add(
        Transaction(
            user_id=user.id,
            transaction_date=date(2026, 1, 6),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("2.0000"),
            unit_price_usd=Decimal("100.0000"),
            fee_usd=Decimal("0"),
            fx_rate_recorded=_FX,
            total_amount_myr=Decimal("890.0000"),
            notes="",
        )
    )
    await db_session.commit()
    verdict = await ips_enforcement.validate_action(
        db_session,
        user,
        {
            "kind": ips_enforcement.ACTION_TRANSACTION,
            "type": "SELL",
            "asset_symbol": "VOO",
            "date": "2026-03-01",
        },
    )
    # Despite BLOCK config, min-holding stays WARN -> allowed, surfaced as a
    # warning (never a blocking violation).
    assert verdict.allowed is True
    assert verdict.max_level == IpsEnforcementLevel.WARN.value
    assert all(w.level != IpsEnforcementLevel.BLOCK.value for w in verdict.warnings)


# --------------------------------------------------------------------------- #
# Override path is audited as a critical IPS_ALERT                             #
# --------------------------------------------------------------------------- #
async def test_override_records_critical_audit(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("TSLA"), override=True
    )
    # With override, the BLOCK violation is allowed but still reported.
    assert verdict.allowed is True
    assert len(verdict.violations) == 1

    # The enforcement point persists a critical IPS_ALERT for the override.
    await ips_enforcement.record_block_audit(
        db_session, user, verdict.violations[0], override=True
    )
    await db_session.commit()
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.event_type == AuditEventType.IPS_ALERT.value,
                AuditLog.severity == AuditSeverity.CRITICAL.value,
            )
        )
    ).scalars().all()
    assert any(row.action == "IPS_BLOCK_OVERRIDDEN" for row in rows)


async def test_block_audit_records_rejection(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    verdict = await ips_enforcement.validate_action(
        db_session, user, _buy_action("TSLA")
    )
    await ips_enforcement.record_block_audit(
        db_session, user, verdict.violations[0], override=False
    )
    await db_session.commit()
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.event_type == AuditEventType.IPS_ALERT.value,
            )
        )
    ).scalars().all()
    assert any(row.action == "IPS_BLOCK" for row in rows)


# --------------------------------------------------------------------------- #
# Three-tier compliance score math (hand-computed)                            #
# --------------------------------------------------------------------------- #
async def test_compliance_score_clean_is_100(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # No transactions, no prices -> no violations -> full score.
    score = await ips_enforcement.compute_compliance_score(db_session, user)
    assert score == 100


async def test_compliance_score_block_deduction(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Hold one forbidden asset (TSLA, BLOCK) -> 100 − 15 = 85.
    from app.models.transaction import Transaction

    db_session.add_all(
        [
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 5),
                transaction_type="DEPOSIT",
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("4450.0000"),
                fee_usd=Decimal("0"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="TSLA",
                quantity=Decimal("1.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("445.0000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()
    score = await ips_enforcement.compute_compliance_score(db_session, user)
    assert score == 85  # 100 − 15 (one BLOCK violation)


async def test_compliance_score_warn_drift_and_info_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # 100% VOO with idle cash so drift (WARN) and cash drag (INFO) both fire.
    # Deposit $10,000, buy 70 VOO ($7,000) -> 70% VOO weight on a $10k NAV (cash
    # $3,000 = 30% > 5% policy). Drift: VOO 70 vs 70 = 0; QQQ 0 vs 30 -> 30pp.
    #   Score = 100 − 7 (WARN drift) − 2 (INFO cash drag) = 91.
    from app.models.transaction import Transaction

    db_session.add_all(
        [
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 5),
                transaction_type="DEPOSIT",
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("44500.0000"),
                fee_usd=Decimal("0"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="VOO",
                quantity=Decimal("70.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("31150.0000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()
    score = await ips_enforcement.compute_compliance_score(
        db_session, user, prices={"VOO": Decimal("100")}, fx_rate=_FX
    )
    # QQQ underweight 30pp > 3pp -> WARN drift (−7); cash 30% > 5% -> INFO (−2).
    assert score == 91


async def test_compliance_score_approaching_deduction(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # 72.5/27.5 -> 2.5pp drift: within 3pp threshold but > 0.7×3 = 2.1pp.
    #   No drift/cash violation; only the approaching penalty: 100 − 3 = 97.
    from app.models.transaction import Transaction

    db_session.add_all(
        [
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 5),
                transaction_type="DEPOSIT",
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("44500.0000"),
                fee_usd=Decimal("0"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="VOO",
                quantity=Decimal("72.5000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("32262.5000"),
                notes="",
            ),
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="QQQ",
                quantity=Decimal("27.5000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("12237.5000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()
    score = await ips_enforcement.compute_compliance_score(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
    )
    assert score == 97  # 100 − 3 (approaching threshold)


async def test_unknown_action_kind_rejected(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    with pytest.raises(ValidationFailed):
        await ips_enforcement.validate_action(
            db_session, user, {"kind": "NONSENSE"}
        )

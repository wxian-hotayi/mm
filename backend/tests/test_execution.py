"""Unified Execution Window Engine + plan generation (DESIGN §19.6, DL 19).

There is exactly ONE scheduler — :func:`classify_window`. These tests pin its
boundaries exactly (Mar/Jun/Sep/Dec deploy, Jun/Dec rebalance, REBALANCE ⊂
DEPLOYMENT, window-day edges), then drive plan generation through a real
:class:`AsyncSession` to assert the DEPLOY cash-only plan never overspends (exact
4dp shares), the REBALANCE plan corrects allocation, every plan is IPS-validated,
and approve blocks on a forbidden-asset order. Money/shares are exact Decimals.

Default IPS window config: anchor 3, deploy interval 3, rebalance interval 6,
window 21 days.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.models.cash import CashAccountType, CashMovementType
from app.models.execution import ExecutionPlanKind, ExecutionPlanStatus
from app.models.ips import IpsRule
from app.services import cash, execution
from conftest import UserFactory, UserLoader
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

_FX = Decimal("4.45")


# =========================================================================== #
# classify_window — pure, exact boundaries (no DB)                            #
# =========================================================================== #
async def test_classify_window_deploy_months(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # Day 1 of each quarterly month is a deployment window. Mar & Dec etc. are
    # checked here; Jun/Dec are rebalance (superset) and asserted below.
    assert execution.classify_window(date(2026, 3, 1), ips) == "DEPLOYMENT"
    assert execution.classify_window(date(2026, 9, 1), ips) == "DEPLOYMENT"


async def test_classify_window_rebalance_months(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # Jun & Dec are rebalance windows (also deployment windows by construction).
    assert execution.classify_window(date(2026, 6, 1), ips) == "REBALANCE"
    assert execution.classify_window(date(2026, 12, 1), ips) == "REBALANCE"


async def test_classify_window_non_window_months(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # Jan/Feb/Apr/May/Jul/Aug/Oct/Nov are never windows on day 1.
    for month in (1, 2, 4, 5, 7, 8, 10, 11):
        assert execution.classify_window(date(2026, month, 1), ips) is None, (
            f"month {month} should not open a window"
        )


async def test_rebalance_is_subset_of_deployment(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # Every REBALANCE date is also (by construction) inside a deployment window:
    # is_rebalance implies classify_window is not None.
    for when in (date(2026, 6, 1), date(2026, 12, 1)):
        kind = execution.classify_window(when, ips)
        assert kind == "REBALANCE"
        assert execution.is_rebalance(when, ips) is True
        assert kind is not None  # i.e. it is also a (super)deployment window


async def test_classify_window_day_boundaries(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # Mar window opens day 1, stays open 21 days (inclusive last day Mar 21),
    # and is closed on Mar 22.
    assert execution.classify_window(date(2026, 3, 1), ips) == "DEPLOYMENT"
    assert execution.classify_window(date(2026, 3, 21), ips) == "DEPLOYMENT"
    assert execution.classify_window(date(2026, 3, 22), ips) is None
    # The day before opening is not a window either.
    assert execution.classify_window(date(2026, 2, 28), ips) is None


async def test_current_open_window_reports_close_date(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    window = execution.current_open_window(date(2026, 6, 10), ips)
    assert window is not None
    assert window.kind == "REBALANCE"
    assert window.opens == date(2026, 6, 1)
    # opens + 21 days − 1 = Jun 21 inclusive.
    assert window.closes == date(2026, 6, 21)
    # Outside any window -> None.
    assert execution.current_open_window(date(2026, 4, 15), ips) is None


async def test_next_window_skips_to_next_opening(
    ips_factory: Callable[..., IpsRule]
) -> None:
    ips = ips_factory()
    # From Apr 15 (no window) the next opening is Jun 1, a REBALANCE window.
    open_date, kind = execution.next_window(date(2026, 4, 15), ips)
    assert open_date == date(2026, 6, 1)
    assert kind == "REBALANCE"
    # Inside an open window, next_window reports that same window.
    open_now, kind_now = execution.next_window(date(2026, 3, 5), ips)
    assert open_now == date(2026, 3, 1)
    assert kind_now == "DEPLOYMENT"


async def test_classify_window_invalid_config_rejected(
    ips_factory: Callable[..., IpsRule]
) -> None:
    # rebalance interval must be a positive multiple of the deployment interval.
    bad = ips_factory(deployment_interval_months=3, rebalance_interval_months=4)
    with pytest.raises(ValidationFailed):
        execution.classify_window(date(2026, 6, 1), bad)


# =========================================================================== #
# Plan generation — DEPLOY (cash-only, exact shares, never overspend)         #
# =========================================================================== #
async def _fund_buffer(
    db_session, user, amount: Decimal, when: date = date(2026, 1, 5)
) -> None:
    account = await cash.create_account(
        db_session,
        user,
        name="GXBank",
        account_type=CashAccountType.GXBANK,
        is_buffer_source=True,
    )
    await cash.create_movement(
        db_session,
        user,
        account_id=account.id,
        movement_type=CashMovementType.INFLOW,
        amount_myr=amount,
        movement_date=when,
    )


async def test_deploy_plan_cash_only_exact_shares(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # RM4,450 buffer surplus -> USD 4,450 / 4.45 = $1,000.00 to deploy. With an
    # empty portfolio and 70/30 targets at $100 prices: target VOO $700, QQQ
    # $300 -> 7.0000 VOO + 3.0000 QQQ exactly (cash-only, buys only).
    await _fund_buffer(db_session, user, Decimal("4450"))
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY,
    )
    assert plan.plan_kind == ExecutionPlanKind.DEPLOY.value
    assert plan.status == ExecutionPlanStatus.DRAFT.value

    import json

    orders = json.loads(plan.orders)
    # Every order is a BUY (deploy windows never sell).
    assert {o["side"] for o in orders} == {"BUY"}
    by_symbol = {o["symbol"]: o for o in orders}
    assert Decimal(by_symbol["VOO"]["quantity"]) == Decimal("7.0000")
    assert Decimal(by_symbol["QQQ"]["quantity"]) == Decimal("3.0000")
    # USD spent (7×100 + 3×100 = $1,000) never exceeds the deployable $1,000.
    spent = sum(Decimal(o["est_amount_usd"]) for o in orders)
    assert spent == Decimal("1000.0000")
    assert plan.cash_deployed_usd == Decimal("1000.0000")
    # MYR deployed = 1,000 × 4.45 = RM4,450.00.
    assert plan.cash_deployed_myr == Decimal("4450.00")


async def test_deploy_plan_floors_and_never_overspends(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # RM4,477.85 -> USD 1,006.2584... ; at VOO $470 / QQQ $400 the CASH_ONLY
    # path floors shares so the plan can never overspend the deployable cash.
    await _fund_buffer(db_session, user, Decimal("4477.85"))
    deployable_usd = (Decimal("4477.85") / _FX)  # ≈ 1006.2584
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("470"), "QQQ": Decimal("400")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY,
    )
    import json

    orders = json.loads(plan.orders)
    spent = sum(Decimal(o["est_amount_usd"]) for o in orders)
    # Invariant: USD spent must not exceed the deployable USD (floored shares).
    assert spent <= deployable_usd
    # All quantities are 4dp.
    for order in orders:
        quantity = Decimal(order["quantity"])
        assert quantity == quantity.quantize(Decimal("0.0001"))


# =========================================================================== #
# Plan generation — REBALANCE allocation correction                          #
# =========================================================================== #
async def _seed_drifted_portfolio(db_session, user) -> None:
    """100 VOO / 0 QQQ: a heavily drifted (100% VOO) portfolio."""
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
                quantity=Decimal("100.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("44500.0000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()


async def test_rebalance_plan_corrects_allocation(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _seed_drifted_portfolio(db_session, user)
    # No fresh cash -> a pure REBALANCE: 100% VOO must be corrected toward 70/30
    # by selling VOO and buying QQQ (sell-last but unavoidable with no cash).
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.REBALANCE,
    )
    assert plan.plan_kind == ExecutionPlanKind.REBALANCE.value
    import json

    orders = json.loads(plan.orders)
    sides = {o["symbol"]: o["side"] for o in orders}
    # VOO (overweight) is sold; QQQ (underweight) is bought — allocation
    # correction toward the 70/30 target.
    assert sides["VOO"] == "SELL"
    assert sides["QQQ"] == "BUY"
    # Post-trade weights move toward 70/30 (VOO close to 70, QQQ close to 30).
    after = json.loads(plan.allocation_after)
    assert Decimal(after["VOO"]) == Decimal("70.0000")
    assert Decimal(after["QQQ"]) == Decimal("30.0000")


async def test_rebalance_window_folds_in_deployable_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _seed_drifted_portfolio(db_session, user)
    # Deployable cash present in a rebalance window -> DEPLOY_AND_REBALANCE
    # (fresh cash is folded in first so selling stays last-resort).
    await _fund_buffer(db_session, user, Decimal("8900"), when=date(2026, 6, 1))
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY_AND_REBALANCE,
    )
    assert plan.plan_kind == ExecutionPlanKind.DEPLOY_AND_REBALANCE.value
    assert plan.cash_deployed_myr > Decimal("0")


# =========================================================================== #
# Plan IPS validation + approve blocking                                      #
# =========================================================================== #
async def test_generated_plan_is_ips_validated_clean(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _fund_buffer(db_session, user, Decimal("4450"))
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY,
    )
    # VOO/QQQ are allowed symbols -> the plan is IPS-compliant with no violations.
    assert plan.ips_compliant is True
    import json

    assert json.loads(plan.ips_violations) == []
    # Approving a clean plan succeeds and links/creates a deployment intent.
    approved = await execution.approve(db_session, user, plan.id)
    assert approved.status == ExecutionPlanStatus.APPROVED.value


async def test_approve_blocks_forbidden_asset_order(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Persist a DRAFT plan whose order references a forbidden symbol (TSLA is not
    # in the allowed VOO/QQQ list -> a forbidden individual stock, BLOCK level).
    from app.models.execution import ExecutionPlan
    import json

    plan = ExecutionPlan(
        user_id=user.id,
        window_date=date(2026, 6, 1),
        plan_kind=ExecutionPlanKind.DEPLOY.value,
        status=ExecutionPlanStatus.DRAFT.value,
        orders=json.dumps(
            [{"symbol": "TSLA", "side": "BUY", "quantity": "1.0000"}]
        ),
    )
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)

    # Approve re-validates IPS and a BLOCK without override rejects with 422.
    with pytest.raises(ValidationFailed):
        await execution.approve(db_session, user, plan.id)

    # An audited override is the sole bypass -> approves despite the violation.
    overridden = await execution.approve(
        db_session, user, plan.id, override=True
    )
    assert overridden.status == ExecutionPlanStatus.APPROVED.value
    # The override is recorded as a critical IPS_ALERT.
    from app.models.audit import AuditEventType, AuditLog, AuditSeverity

    alerts = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.event_type == AuditEventType.IPS_ALERT.value,
                AuditLog.severity == AuditSeverity.CRITICAL.value,
            )
        )
    ).scalars().all()
    assert any(row.action == "IPS_BLOCK_OVERRIDDEN" for row in alerts)


async def test_plan_lifecycle_execute_and_skip(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _fund_buffer(db_session, user, Decimal("4450"))
    plan = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY,
    )
    approved = await execution.approve(db_session, user, plan.id)
    executed = await execution.execute(db_session, user, approved.id)
    assert executed.status == ExecutionPlanStatus.EXECUTED.value
    assert executed.executed_at is not None

    # A fresh DRAFT plan can be skipped (discipline-preserving).
    plan2 = await execution.generate_execution_plan(
        db_session,
        user,
        prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
        fx_rate=_FX,
        kind=ExecutionPlanKind.DEPLOY,
    )
    skipped = await execution.skip(db_session, user, plan2.id)
    assert skipped.status == ExecutionPlanStatus.SKIPPED.value


async def test_generate_plan_rejects_non_positive_fx(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    with pytest.raises(ValidationFailed):
        await execution.generate_execution_plan(
            db_session,
            user,
            prices={"VOO": Decimal("100"), "QQQ": Decimal("100")},
            fx_rate=Decimal("0"),
            kind=ExecutionPlanKind.DEPLOY,
        )

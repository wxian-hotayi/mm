"""Action Status Engine (DESIGN §19.3) — the single system-wide signal.

Output ∈ {DO_NOTHING, REVIEW_REQUIRED, REBALANCE_NOW}, composed deterministically
from the upstream engines (cycle, IPS enforcement, behavior, drift, cash, window
schedule, DRAFT plans). These tests construct the signal sets that drive each of
the three outputs, confirm DO_NOTHING is the disciplined default, and check the
``signals`` block is populated. Each test uses its own isolated user and an
explicit ``today`` so window-relative triggers are deterministic.

Default IPS: drift threshold 3pp, review_lead_days 14, windows Mar/Jun/Sep/Dec
(Jun/Dec rebalance). A "quiet" day (Apr 15) is clear of every window and >14
days from the next opening (Jun 1), so the window-soon trigger never fires there.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.models.execution import ExecutionPlan, ExecutionPlanKind, ExecutionPlanStatus
from app.services import action_status, cash
from conftest import UserFactory, UserLoader

pytestmark = pytest.mark.asyncio(loop_scope="session")

_FX = Decimal("4.45")
_QUIET_DAY = date(2026, 4, 15)
_ON_TARGET_PRICES = {"VOO": Decimal("100"), "QQQ": Decimal("100")}


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


async def _on_target_portfolio(db_session, user) -> None:
    """70 VOO / 30 QQQ at equal prices -> exactly on the 70/30 target."""
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
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 1, 6),
                transaction_type="BUY",
                asset_symbol="QQQ",
                quantity=Decimal("30.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("13350.0000"),
                notes="",
            ),
        ]
    )
    await db_session.commit()


async def _drifted_portfolio(db_session, user) -> None:
    """100% VOO -> max|drift| 30pp, far beyond the 3pp threshold."""
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


def _codes(status: action_status.ActionStatus) -> set[str]:
    return {reason.code for reason in status.reasons}


# --------------------------------------------------------------------------- #
# DO_NOTHING — the disciplined default                                         #
# --------------------------------------------------------------------------- #
async def test_do_nothing_default(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _on_target_portfolio(db_session, user)
    # On-target, no deployable cash, no window, no flags -> DO_NOTHING.
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=_QUIET_DAY,
    )
    assert status.status == action_status.STATUS_DO_NOTHING
    assert status.label == "Do Nothing"
    assert _codes(status) == {action_status.REASON_ON_TRACK}
    # Signals are populated and drift is exactly on target (0pp).
    assert status.signals["max_drift_pp"] == "0.0000"
    assert status.signals["deployable_myr"] == "0.0000"
    assert status.signals["behavior_flag_count"] == 0
    assert status.signals["ips_violation_count"] == 0
    assert status.compliance_score == 100


# --------------------------------------------------------------------------- #
# REVIEW_REQUIRED — each individual trigger                                    #
# --------------------------------------------------------------------------- #
async def test_review_deployable_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Deployable cash ready (READY_TO_DEPLOY), nothing else -> REVIEW_REQUIRED.
    await _fund_buffer(db_session, user, Decimal("3000"))
    status = await action_status.compute(db_session, user, today=_QUIET_DAY)
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_DEPLOYABLE_CASH in _codes(status)
    assert status.signals["deployable_myr"] == "3000.0000"


async def test_review_ips_violation(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Hold a forbidden symbol (TSLA) -> a standing IPS alert -> REVIEW_REQUIRED.
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
    status = await action_status.compute(db_session, user, today=_QUIET_DAY)
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_IPS_VIOLATION in _codes(status)
    assert status.signals["ips_violation_count"] >= 1
    # A BLOCK-level held forbidden asset lowers the compliance score below 100.
    assert status.compliance_score < 100


async def test_review_behavior_flag(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # >=3 BUY/SELL within a rolling 7-day window in the last 30 days, dated near
    # the evaluation day, triggers the HIGH_FREQUENCY_TRADING flag (WARNING).
    from app.models.transaction import Transaction

    today = date(2026, 4, 15)
    db_session.add(
        Transaction(
            user_id=user.id,
            transaction_date=date(2026, 4, 1),
            transaction_type="DEPOSIT",
            fx_rate_recorded=_FX,
            total_amount_myr=Decimal("44500.0000"),
            fee_usd=Decimal("0"),
            notes="",
        )
    )
    for day in (10, 11, 12):
        db_session.add(
            Transaction(
                user_id=user.id,
                transaction_date=date(2026, 4, day),
                transaction_type="BUY",
                asset_symbol="VOO",
                quantity=Decimal("1.0000"),
                unit_price_usd=Decimal("100.0000"),
                fee_usd=Decimal("0"),
                fx_rate_recorded=_FX,
                total_amount_myr=Decimal("445.0000"),
                notes="",
            )
        )
    await db_session.commit()
    status = await action_status.compute(db_session, user, today=today)
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_BEHAVIOR_FLAG in _codes(status)
    assert status.signals["behavior_flag_count"] >= 1


async def test_review_approaching_drift(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Build a portfolio drifted to 72.5/27.5 -> max|drift| 2.5pp: within the 3pp
    # threshold but above 0.7×3 = 2.1pp -> approaching -> REVIEW_REQUIRED.
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
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=_QUIET_DAY,
    )
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_DRIFT_APPROACHING in _codes(status)
    # 72.5 − 70 = 2.5pp drift, populated in signals.
    assert status.signals["max_drift_pp"] == "2.5000"


async def test_review_window_soon(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _on_target_portfolio(db_session, user)
    # May 25 is 7 days before the Jun 1 window opening (<= review_lead_days 14)
    # and not itself inside a window -> WINDOW_SOON -> REVIEW_REQUIRED.
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=date(2026, 5, 25),
    )
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_WINDOW_SOON in _codes(status)


async def test_review_draft_plan(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _on_target_portfolio(db_session, user)
    # A DRAFT plan awaiting approval -> REVIEW_REQUIRED.
    plan = ExecutionPlan(
        user_id=user.id,
        window_date=date(2026, 3, 1),
        plan_kind=ExecutionPlanKind.DEPLOY.value,
        status=ExecutionPlanStatus.DRAFT.value,
    )
    db_session.add(plan)
    await db_session.commit()
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=_QUIET_DAY,
    )
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert action_status.REASON_DRAFT_PLAN in _codes(status)


# --------------------------------------------------------------------------- #
# REBALANCE_NOW — only inside a rebalance window with drift beyond threshold   #
# --------------------------------------------------------------------------- #
async def test_rebalance_now_in_window_with_drift(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _drifted_portfolio(db_session, user)
    # Jun 10 is inside the Jun REBALANCE window; max|drift| 30pp > 3pp -> the
    # only status that asks the user to actively trade.
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=date(2026, 6, 10),
    )
    assert status.status == action_status.STATUS_REBALANCE_NOW
    assert status.label == "Rebalance Now"
    assert action_status.REASON_REBALANCE_DRIFT in _codes(status)
    assert status.signals["max_drift_pp"] == "30.0000"
    # The next rebalance window date is reported.
    assert status.next_rebalance_date is not None


async def test_drift_beyond_outside_window_is_review_not_rebalance(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _drifted_portfolio(db_session, user)
    # Same heavy drift but on a quiet (non-window) day: REBALANCE_NOW requires an
    # open rebalance window, so this surfaces as REVIEW_REQUIRED instead
    # (the drift is reported via the IPS alert, not a forced trade).
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=_QUIET_DAY,
    )
    assert status.status == action_status.STATUS_REVIEW_REQUIRED
    assert status.status != action_status.STATUS_REBALANCE_NOW


async def test_rebalance_window_without_drift_is_not_rebalance_now(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _on_target_portfolio(db_session, user)
    # Inside the Jun rebalance window but on target (drift 0pp): REBALANCE_NOW is
    # NOT triggered (it needs drift>threshold or overdue). With a window opening
    # "now" the status is REVIEW_REQUIRED (window soon), never DO_NOTHING here.
    status = await action_status.compute(
        db_session,
        user,
        prices=_ON_TARGET_PRICES,
        fx_rate=_FX,
        today=date(2026, 6, 10),
    )
    assert status.status != action_status.STATUS_REBALANCE_NOW

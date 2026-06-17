"""Wealth Operating Cycle Engine (DESIGN §19.2) — derived state machine.

The current life-cycle state is a pure function of (date vs windows, deployable
cash, drift, active intents/plans) and is never stored authoritatively
(Decision Log 21). These tests drive :mod:`app.services.cycle` through a real
:class:`AsyncSession`, supplying an explicit ``today`` so the deterministic
precedence and window-day boundaries are exercised without depending on the
wall clock. Each test uses its own isolated user.

Default IPS window config (from the seeded IPS row, made explicit in the
fixtures): anchor month 3, deployment interval 3, rebalance interval 6, window
length 21 days. Hence DEPLOYMENT windows open Mar/Jun/Sep/Dec (day 1–21) and
the Jun/Dec ones are also REBALANCE windows.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.cash import CashAccountType, CashMovementType
from app.models.cycle import CycleStateLog, WealthCycleState
from app.models.deployment import DeploymentTrigger
from app.services import cash, cycle, deployment
from conftest import UserFactory, UserLoader
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Prices/FX that keep a 70/30 VOO/QQQ ledger exactly on target (no drift).
_BALANCED_PRICES = {"VOO": Decimal("100"), "QQQ": Decimal("100")}
_FX = Decimal("4.45")
# A non-window weekday well clear of every Mar/Jun/Sep/Dec opening.
_QUIET_DAY = date(2026, 4, 15)


async def _fund_buffer(
    db_session,
    user,
    amount: Decimal,
    *,
    when: date = date(2026, 1, 5),
) -> None:
    """Create a buffer-source GXBank account and fund it with ``amount`` MYR."""
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


# --------------------------------------------------------------------------- #
# Precedence rule 4: ACCUMULATION (the default)                                #
# --------------------------------------------------------------------------- #
async def test_accumulation_default(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # No cash, no window, no intents/plans -> the disciplined default.
    state = await cycle.current_state(db_session, user, today=_QUIET_DAY)
    assert state.state == WealthCycleState.ACCUMULATION
    assert state.context["open_window"] is False
    assert state.context["window_kind"] is None
    assert state.context["active_intents"] == 0
    assert state.context["unexecuted_plans"] == 0


# --------------------------------------------------------------------------- #
# Precedence rule 3: READY_TO_DEPLOY at threshold, no open window              #
# --------------------------------------------------------------------------- #
async def test_ready_to_deploy_at_threshold_outside_window(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Surplus exactly at the RM1,500 threshold but no open window / pending work
    # -> READY_TO_DEPLOY (cash ready, not yet acting).
    await _fund_buffer(db_session, user, Decimal("1500"))
    state = await cycle.current_state(db_session, user, today=_QUIET_DAY)
    assert state.state == WealthCycleState.READY_TO_DEPLOY
    assert state.context["deployable_myr"] == "1500.0000"


async def test_below_threshold_stays_accumulation(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # One ringgit below threshold -> still ACCUMULATION.
    await _fund_buffer(db_session, user, Decimal("1499"))
    state = await cycle.current_state(db_session, user, today=_QUIET_DAY)
    assert state.state == WealthCycleState.ACCUMULATION


# --------------------------------------------------------------------------- #
# Precedence rule 2: DEPLOYMENT                                                #
# --------------------------------------------------------------------------- #
async def test_deployment_with_active_intent(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # An active QUEUED intent forces DEPLOYMENT even with no open window and no
    # cash modelled (intent presence dominates READY/ACCUMULATION).
    await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    state = await cycle.current_state(db_session, user, today=_QUIET_DAY)
    assert state.state == WealthCycleState.DEPLOYMENT
    assert state.context["active_intents"] == 1


async def test_deployment_in_window_with_ready_cash(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Mar 10 is inside the Mar deployment window (deploy-only, NOT rebalance);
    # surplus over threshold -> DEPLOYMENT.
    await _fund_buffer(db_session, user, Decimal("3000"), when=date(2026, 3, 1))
    state = await cycle.current_state(db_session, user, today=date(2026, 3, 10))
    assert state.state == WealthCycleState.DEPLOYMENT
    assert state.context["window_kind"] == "DEPLOYMENT"
    assert state.context["open_window"] is True


async def test_deploy_window_without_cash_is_accumulation(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Open Mar deploy window but no deployable cash and no pending work: a deploy
    # window alone does not force DEPLOYMENT (it needs cash ready) -> the default
    # ACCUMULATION (a deploy-only window is not a REBALANCE window either).
    state = await cycle.current_state(db_session, user, today=date(2026, 3, 10))
    assert state.state == WealthCycleState.ACCUMULATION
    assert state.context["window_kind"] == "DEPLOYMENT"


# --------------------------------------------------------------------------- #
# Precedence rule 1: REBALANCE_WINDOW                                          #
# --------------------------------------------------------------------------- #
async def test_rebalance_window_in_jun(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Jun 10 is inside the Jun window, which is a REBALANCE window (rule 1 wins
    # over everything, even with no drift signal supplied).
    state = await cycle.current_state(db_session, user, today=date(2026, 6, 10))
    assert state.state == WealthCycleState.REBALANCE_WINDOW
    assert state.context["window_kind"] == "REBALANCE"


async def test_rebalance_window_precedes_deployment(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Even with an active intent (which alone would be DEPLOYMENT), being inside
    # a REBALANCE window wins by precedence (rule 1 before rule 2).
    await deployment.enqueue(
        db_session,
        user,
        trigger=DeploymentTrigger.MANUAL,
        amount_myr=Decimal("1500"),
    )
    state = await cycle.current_state(db_session, user, today=date(2026, 12, 10))
    assert state.state == WealthCycleState.REBALANCE_WINDOW


# --------------------------------------------------------------------------- #
# Window-day boundary edges (open day 1, last open day, first closed day)      #
# --------------------------------------------------------------------------- #
async def test_window_day_boundaries(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    await _fund_buffer(db_session, user, Decimal("3000"), when=date(2026, 3, 1))

    # Day 1 (Mar 1): window open -> DEPLOYMENT (deploy window + ready cash).
    state_open = await cycle.current_state(
        db_session, user, today=date(2026, 3, 1)
    )
    assert state_open.context["open_window"] is True
    assert state_open.state == WealthCycleState.DEPLOYMENT

    # Day 21 (Mar 21): last inclusive open day -> still open.
    state_last = await cycle.current_state(
        db_session, user, today=date(2026, 3, 21)
    )
    assert state_last.context["open_window"] is True
    assert state_last.state == WealthCycleState.DEPLOYMENT

    # Day 22 (Mar 22): window has closed -> no open window. Cash still ready so
    # the state falls through to READY_TO_DEPLOY (not DEPLOYMENT).
    state_closed = await cycle.current_state(
        db_session, user, today=date(2026, 3, 22)
    )
    assert state_closed.context["open_window"] is False
    assert state_closed.state == WealthCycleState.READY_TO_DEPLOY


# --------------------------------------------------------------------------- #
# Overdue-rebalance: a passed rebalance window with drift beyond threshold     #
# --------------------------------------------------------------------------- #
async def test_rebalance_overdue_with_drift(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    # Build a drifted 100% VOO ledger so max|drift| (30pp) is beyond the 3pp
    # threshold. First buy in Jan 2026; the Jun 2026 rebalance window then fully
    # passes by Sep -> overdue (no prior executed rebalance).
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

    drifted_prices = {"VOO": Decimal("100"), "QQQ": Decimal("100")}
    # As-of Sep 15 (outside any rebalance window): the Jun rebalance window has
    # fully passed while drift is beyond threshold -> overdue -> REBALANCE_WINDOW.
    state = await cycle.current_state(
        db_session,
        user,
        prices=drifted_prices,
        fx_rate=_FX,
        today=date(2026, 9, 15),
    )
    assert state.context["rebalance_overdue"] is True
    assert state.state == WealthCycleState.REBALANCE_WINDOW
    # The drift signal is populated when priced.
    assert state.context["drift_max_pp"] == "30.0000"


async def test_not_overdue_when_drift_unknown(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
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
    # No prices/FX -> drift unknown -> overdue check is False (documented
    # fallback). Sep 15 is not in any window -> ACCUMULATION.
    state = await cycle.current_state(db_session, user, today=date(2026, 9, 15))
    assert state.context["drift_max_pp"] is None
    assert state.context["rebalance_overdue"] is False
    assert state.state == WealthCycleState.ACCUMULATION


# --------------------------------------------------------------------------- #
# Transition logging dedupe (Decision Log 21)                                  #
# --------------------------------------------------------------------------- #
async def test_transition_logging_dedupe(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)

    async def _log_count() -> int:
        rows = await db_session.execute(
            select(CycleStateLog).where(CycleStateLog.user_id == user.id)
        )
        return len(rows.scalars().all())

    # First logged evaluation (ACCUMULATION) writes one row.
    await cycle.current_state(db_session, user, today=_QUIET_DAY, log=True)
    assert await _log_count() == 1

    # Re-evaluating the SAME state with log=True writes nothing (dedupe).
    await cycle.current_state(db_session, user, today=_QUIET_DAY, log=True)
    assert await _log_count() == 1

    # A read-only evaluation (log=False) never writes, even if state changed.
    await _fund_buffer(db_session, user, Decimal("1500"))
    await cycle.current_state(db_session, user, today=_QUIET_DAY, log=False)
    assert await _log_count() == 1

    # Now the state is READY_TO_DEPLOY; logging it appends one new row.
    state = await cycle.current_state(
        db_session, user, today=_QUIET_DAY, log=True
    )
    assert state.state == WealthCycleState.READY_TO_DEPLOY
    assert await _log_count() == 2


async def test_log_transition_returns_none_when_unchanged(
    db_session, user_factory: UserFactory, load_user: UserLoader
) -> None:
    seeded = await user_factory()
    user = await load_user(seeded)
    first = await cycle.log_transition(
        db_session, user, WealthCycleState.ACCUMULATION, {}
    )
    assert first is not None
    # Same state again -> dedupe returns None (no row written).
    again = await cycle.log_transition(
        db_session, user, WealthCycleState.ACCUMULATION, {}
    )
    assert again is None

"""XIRR solver and the isolated Investment vs FX return decomposition.

The FX identity under test (DESIGN section 7.3):
(1 + invest_return) x (1 + fx_return) = 1 + total_return_myr,
e.g. +9% investment and +5% FX compound to +14.45% total in MYR.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.models.transaction import Transaction
from app.services.ledger import LedgerState, replay
from app.services.returns import compute_returns
from app.utils.xirr import xirr

TOLERANCE = Decimal("0.0001")


def test_xirr_known_two_flow_case() -> None:
    # -1000 on 2025-01-01, +1100 exactly 365 days later. At the solver's
    # 365-day year convention that is exactly one year, so the annual rate
    # is 1100/1000 - 1 = 10%.
    flows = [
        (date(2025, 1, 1), Decimal("-1000")),
        (date(2026, 1, 1), Decimal("1100")),
    ]
    assert xirr(flows) == Decimal("0.100000")


def test_xirr_bisection_fallback_for_deep_loss() -> None:
    # -1000 turning into +50 over exactly one year is a -95% annual rate:
    # -1000 + 50/(1+r) = 0 -> 1+r = 0.05. Newton from 0.1 overshoots below
    # rate -1 and gives up; the bisection fallback on [-0.9999, 10] finds it.
    flows = [
        (date(2025, 1, 1), Decimal("-1000")),
        (date(2026, 1, 1), Decimal("50")),
    ]
    assert xirr(flows) == Decimal("-0.950000")


def test_xirr_undefined_cases() -> None:
    # Fewer than two flows.
    assert xirr([]) is None
    assert xirr([(date(2025, 1, 1), Decimal("-1000"))]) is None
    # Sign-uniform flows can never cross zero NPV.
    assert (
        xirr(
            [
                (date(2025, 1, 1), Decimal("-1000")),
                (date(2026, 1, 1), Decimal("-1100")),
            ]
        )
        is None
    )
    assert (
        xirr(
            [
                (date(2025, 1, 1), Decimal("1000")),
                (date(2026, 1, 1), Decimal("1100")),
            ]
        )
        is None
    )


def _single_deposit_state(
    txn_builder: Callable[..., Transaction],
) -> LedgerState:
    # One deposit: RM4,000 at FX 4.00 -> $1,000 invested.
    return replay(
        [
            txn_builder(
                transaction_date=date(2026, 1, 5),
                transaction_type="DEPOSIT",
                fx_rate_recorded=Decimal("4.0000"),
                total_amount_myr=Decimal("4000.0000"),
            )
        ]
    )


def test_fx_identity_9_and_5_gives_14_45(
    txn_builder: Callable[..., Transaction],
) -> None:
    # Invested $1,000 (RM4,000 @ 4.00). NAV today $1,090 -> invest = +9%.
    # Current FX 4.20 -> NAV MYR = 1,090 x 4.20 = RM4,578 -> total MYR
    # return = 4578/4000 - 1 = +14.45%. The FX leg must therefore be
    # (1.1445 / 1.09) - 1 = +5.0000% exactly.
    state = _single_deposit_state(txn_builder)
    report = compute_returns(
        state,
        Decimal("1090.0000"),
        Decimal("4.2000"),
        as_of=date(2027, 1, 5),
    )
    assert report.invest_return == Decimal("0.090000")
    assert report.total_return_myr == Decimal("0.144500")
    assert report.fx_return is not None
    assert abs(report.fx_return - Decimal("0.05")) <= TOLERANCE
    assert report.fx_return == Decimal("0.050000")


def test_fx_identity_annualized_block(
    txn_builder: Callable[..., Transaction],
) -> None:
    # The terminal date is exactly 365 days after the deposit, so the
    # annualized XIRR legs equal the simple returns: 9% USD, 14.45% MYR,
    # and the annualized FX leg is again exactly 5%.
    state = _single_deposit_state(txn_builder)
    report = compute_returns(
        state,
        Decimal("1090.0000"),
        Decimal("4.2000"),
        as_of=date(2027, 1, 5),
    )
    assert report.xirr_usd == Decimal("0.090000")
    assert report.xirr_myr == Decimal("0.144500")
    assert report.fx_return_annualized is not None
    assert abs(report.fx_return_annualized - Decimal("0.05")) <= TOLERANCE


def test_all_none_when_no_flows() -> None:
    # An empty ledger has no deposits and fewer than two XIRR flows:
    # every return in the report is undefined (None).
    report = compute_returns(
        LedgerState(), Decimal("0"), Decimal("4.4500"), as_of=date(2026, 6, 1)
    )
    assert report.invest_return is None
    assert report.total_return_myr is None
    assert report.fx_return is None
    assert report.xirr_usd is None
    assert report.xirr_myr is None
    assert report.fx_return_annualized is None


def test_fx_rate_must_be_positive() -> None:
    with pytest.raises(ValidationFailed, match="FX rate"):
        compute_returns(LedgerState(), Decimal("0"), Decimal("0"))

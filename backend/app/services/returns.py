"""Isolated investment vs FX returns (DESIGN section 7.3).

Pure functions over a replayed :class:`~app.services.ledger.LedgerState` and
the current NAV. MYR flows use the FX rate recorded on each transaction;
the terminal MYR value uses the current FX rate, so the difference between
the MYR and USD return is exactly the FX effect.

Units: every return is a dimensionless decimal fraction quantized to 6dp
(e.g. ``Decimal('0.144500')`` = +14.45%) or ``None`` when undefined.
The multiplicative identity holds throughout:
``(1 + invest) × (1 + fx) = 1 + total_myr`` (9% & 5% -> 14.45%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.errors import ValidationFailed
from app.services.ledger import LedgerState
from app.utils.dates import kl_today
from app.utils.money import Q4, Q6, ZERO
from app.utils.xirr import xirr

_ONE: Final[Decimal] = Decimal("1")


@dataclass(frozen=True)
class ReturnsReport:
    """Decomposed portfolio returns; all values 6dp decimal fractions or None.

    Simple (non-annualized) block:
      - ``invest_return``: total USD return on net invested capital,
        ``(nav_usd + Σwithdrawals_usd − Σdeposits_usd) / Σdeposits_usd``.
      - ``total_return_myr``: same in MYR, flows at their recorded FX rates,
        terminal NAV at the current FX rate.
      - ``fx_return``: ``(1 + total_return_myr)/(1 + invest_return) − 1``.

    Annualized block:
      - ``xirr_usd`` / ``xirr_myr``: money-weighted annual rates over the
        external flows plus the terminal NAV.
      - ``fx_return_annualized``: ``(1 + xirr_myr)/(1 + xirr_usd) − 1``.
    """

    invest_return: Decimal | None
    total_return_myr: Decimal | None
    fx_return: Decimal | None
    xirr_usd: Decimal | None
    xirr_myr: Decimal | None
    fx_return_annualized: Decimal | None


def _fx_decomposition(
    total_myr: Decimal | None, invest_usd: Decimal | None
) -> Decimal | None:
    """FX leg of the identity ``(1+invest)(1+fx) = 1+total``; None when either
    input is None or the USD leg equals exactly −100%."""
    if total_myr is None or invest_usd is None:
        return None
    if _ONE + invest_usd == ZERO:
        return None
    return Q6((_ONE + total_myr) / (_ONE + invest_usd) - _ONE)


def compute_returns(
    state: LedgerState,
    nav_usd: Decimal,
    fx_rate: Decimal,
    as_of: date | None = None,
) -> ReturnsReport:
    """Compute simple and annualized returns from ``state.external_flows``.

    ``nav_usd`` is the current portfolio NAV in USD; ``fx_rate`` the current
    USD->MYR rate used only for the terminal MYR value; ``as_of`` dates the
    terminal XIRR flow (defaults to today in Asia/Kuala_Lumpur).
    Raises :class:`ValidationFailed` when ``fx_rate`` is not positive.
    """
    if fx_rate <= ZERO:
        raise ValidationFailed("FX rate (USD->MYR) must be positive")
    terminal_date = as_of if as_of is not None else kl_today()
    nav_myr = Q4(nav_usd * fx_rate)

    deposits_usd = ZERO
    withdrawals_usd = ZERO
    deposits_myr = ZERO
    withdrawals_myr = ZERO
    for _, flow_usd, flow_myr in state.external_flows:
        if flow_usd < ZERO:
            deposits_usd += -flow_usd
        else:
            withdrawals_usd += flow_usd
        if flow_myr < ZERO:
            deposits_myr += -flow_myr
        else:
            withdrawals_myr += flow_myr

    invest_return = (
        Q6((nav_usd + withdrawals_usd - deposits_usd) / deposits_usd)
        if deposits_usd > ZERO
        else None
    )
    total_return_myr = (
        Q6((nav_myr + withdrawals_myr - deposits_myr) / deposits_myr)
        if deposits_myr > ZERO
        else None
    )
    fx_return = _fx_decomposition(total_return_myr, invest_return)

    usd_flows = [
        (flow_date, flow_usd)
        for flow_date, flow_usd, _ in state.external_flows
    ]
    usd_flows.append((terminal_date, nav_usd))
    myr_flows = [
        (flow_date, flow_myr)
        for flow_date, _, flow_myr in state.external_flows
    ]
    myr_flows.append((terminal_date, nav_myr))
    xirr_usd = xirr(usd_flows)
    xirr_myr = xirr(myr_flows)
    fx_return_annualized = _fx_decomposition(xirr_myr, xirr_usd)

    return ReturnsReport(
        invest_return=invest_return,
        total_return_myr=total_return_myr,
        fx_return=fx_return,
        xirr_usd=xirr_usd,
        xirr_myr=xirr_myr,
        fx_return_annualized=fx_return_annualized,
    )

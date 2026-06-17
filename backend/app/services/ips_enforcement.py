"""Three-tier IPS Enforcement Engine (DESIGN §19.5, Decision Log 20).

Turns the Phase-1 stored :class:`~app.models.ips.IpsRule` policy into an
active enforcement layer. Every candidate action — a single ``TRANSACTION``,
a generated ``EXECUTION_PLAN`` (each order), or a Phase-6
``AI_RECOMMENDATION`` (each recommended order) — is run through
:func:`validate_action`, which returns an :class:`EnforcementVerdict`.

Three enforcement tiers (per-rule, stored on ``IpsRule``):

* ``INFO``  — recorded only; no Action-Status / blocking effect beyond the
  record (audited when persisted).
* ``WARN``  — surfaces in Action Status and lowers the compliance score, but
  **allows** execution.
* ``BLOCK`` — rejects the action (the API turns this into HTTP 422). Reserved
  **only** for forbidden asset classes: leverage, options, and instruments
  outside the IPS allowed list. A supplied, audited ``override=True`` is the
  sole bypass.

BLOCK eligibility is asset-class only. The forbidden-asset / leverage /
options rules are the only rules whose level may be ``BLOCK``; the behavioral
rules (drift, min-holding, cash-drag) are clamped to at most ``WARN`` here so
policy drift can never hard-block the user's own ledger (DESIGN §19.5).

Units: money/shares/FX are :class:`decimal.Decimal`; ``*_pp`` figures are
percentage points; compliance scores are plain ``int`` on the 0–100 scale.
Floats never touch any value handled here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.ips import IpsEnforcementLevel, IpsRule
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.services.behavior import _parse_allowed_symbols
from app.services.drift import drift
from app.services.ledger import replay
from app.services.valuation import valuation
from app.utils.dates import add_months, kl_today

# --- Rule-type identifiers (stable strings used in violations + audit) ---
RULE_FORBIDDEN_ASSET: Final[str] = "FORBIDDEN_ASSET"
RULE_LEVERAGE: Final[str] = "LEVERAGE"
RULE_OPTIONS: Final[str] = "OPTIONS"
RULE_DRIFT: Final[str] = "DRIFT"
RULE_MIN_HOLDING: Final[str] = "MIN_HOLDING"
RULE_CASH_DRAG: Final[str] = "CASH_DRAG"

# Action kinds accepted by validate_action.
ACTION_TRANSACTION: Final[str] = "TRANSACTION"
ACTION_EXECUTION_PLAN: Final[str] = "EXECUTION_PLAN"
ACTION_AI_RECOMMENDATION: Final[str] = "AI_RECOMMENDATION"

# Compliance-score deductions (DESIGN §19.5).
_PENALTY_BLOCK: Final[int] = 15
_PENALTY_WARN: Final[int] = 7
_PENALTY_INFO: Final[int] = 2
_PENALTY_APPROACHING: Final[int] = 3
# A drift within this fraction of the threshold is "approaching" (matches the
# Action-Status review trigger in DESIGN §19.3).
_APPROACHING_FRACTION: Final[Decimal] = Decimal("0.7")

# Trade-bearing transaction types whose symbol is subject to asset-class rules.
_TRADE_TYPES: Final[frozenset[str]] = frozenset(
    {TransactionType.BUY.value, TransactionType.SELL.value}
)

# --- Deterministic symbol classification ---------------------------------
#
# A plain-equity ticker is 1–5 uppercase letters, optionally with a single
# class suffix (e.g. "BRK.B"). Anything not matching, or carrying an option /
# leverage marker, is treated as a forbidden instrument. The rules are
# intentionally simple and deterministic — no external instrument database.
_PLAIN_EQUITY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

# OCC-style option symbol: root + YYMMDD + C|P + strike (e.g. "AAPL240119C00150000").
_OCC_OPTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Z]{1,6}\d{6}[CP]\d{1,8}$"
)
# Standalone option markers (matched as whole tokens, separators stripped).
_OPTION_TOKENS: Final[frozenset[str]] = frozenset(
    {"CALL", "PUT", "OPT", "OPTION"}
)
# Leverage markers (matched as substrings after separator stripping).
_LEVERAGE_TOKENS: Final[tuple[str, ...]] = (
    "LEVERAGED",
    "BULL",
    "BEAR",
    "2X",
    "3X",
    "X2",
    "X3",
    "2L",
    "3L",
    "ULTRA",
)


def _normalize_symbol(symbol: str) -> str:
    """Upper-case and strip surrounding whitespace from a raw symbol."""
    return symbol.strip().upper()


def _tokens(symbol: str) -> set[str]:
    """Split ``symbol`` on common separators ( -._/ space) into upper tokens."""
    return {t for t in re.split(r"[\s\-._/]+", symbol) if t}


def is_option_symbol(symbol: str) -> bool:
    """Return True when ``symbol`` deterministically looks like an option.

    A symbol is an option when it matches the OCC contract pattern
    (root + 6-digit expiry + C/P + strike) or carries a standalone option
    token (CALL/PUT/OPT/OPTION). Case-insensitive; separators are ignored.
    """
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return False
    if _OCC_OPTION_RE.match(normalized.replace(" ", "")):
        return True
    return bool(_tokens(normalized) & _OPTION_TOKENS)


def is_leverage_symbol(symbol: str) -> bool:
    """Return True when ``symbol`` deterministically looks like a leveraged
    instrument (carries a leverage marker such as 2X/3X/LEVERAGED/BULL/BEAR).
    Case-insensitive; separators are ignored."""
    normalized = _normalize_symbol(symbol).replace(" ", "")
    if not normalized:
        return False
    return any(marker in normalized for marker in _LEVERAGE_TOKENS)


def is_plain_equity_symbol(symbol: str) -> bool:
    """Return True when ``symbol`` matches a plain-equity ticker shape and is
    neither an option nor a leveraged instrument."""
    normalized = _normalize_symbol(symbol)
    if is_option_symbol(normalized) or is_leverage_symbol(normalized):
        return False
    return bool(_PLAIN_EQUITY_RE.match(normalized))


# --- Enforcement-level ordering ------------------------------------------
_LEVEL_ORDER: Final[dict[str, int]] = {
    IpsEnforcementLevel.INFO.value: 0,
    IpsEnforcementLevel.WARN.value: 1,
    IpsEnforcementLevel.BLOCK.value: 2,
}


def level_rank(level: str) -> int:
    """Return the ordinal rank of an enforcement level (INFO<WARN<BLOCK).

    Raises :class:`ValidationFailed` on an unknown level string.
    """
    try:
        return _LEVEL_ORDER[level]
    except KeyError as exc:
        raise ValidationFailed(
            f"Unknown IPS enforcement level {level!r}"
        ) from exc


def max_level(levels: Sequence[str]) -> str | None:
    """Return the highest-ranked enforcement level in ``levels`` (None when
    empty)."""
    if not levels:
        return None
    return max(levels, key=level_rank)


def _clamp_behavioral(level: str) -> str:
    """Clamp a behavioral-rule level to at most WARN.

    Behavioral / policy rules (drift, min-holding, cash-drag) are never
    BLOCK-eligible (DESIGN §19.5): if an operator configured one to ``BLOCK``
    it is downgraded to ``WARN`` so policy can never hard-block the ledger.
    """
    if level_rank(level) >= level_rank(IpsEnforcementLevel.BLOCK.value):
        return IpsEnforcementLevel.WARN.value
    return level


# --- Result shapes --------------------------------------------------------
@dataclass(frozen=True)
class IpsViolation:
    """One IPS rule violation.

    ``rule_type`` is one of the ``RULE_*`` constants; ``level`` is the
    effective :class:`~app.models.ips.IpsEnforcementLevel` value
    (already clamped for behavioral rules); ``evidence`` is a
    JSON-serializable mapping (dates as ISO strings, Decimals as strings).
    """

    rule_type: str
    level: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnforcementVerdict:
    """Verdict for a validated action.

    ``allowed`` is False iff a ``BLOCK``-level violation exists and the caller
    did not pass ``override=True``. ``max_level`` is the highest level across
    all violations (``None`` when clean). ``violations`` lists BLOCK-level
    violations; ``warnings`` lists WARN/INFO violations (which never block).
    """

    allowed: bool
    max_level: str | None
    violations: list[IpsViolation]
    warnings: list[IpsViolation]


# --- IPS loading ----------------------------------------------------------
async def _load_ips(db: AsyncSession, user: User) -> IpsRule:
    """Load the user's single :class:`IpsRule`; raise NotFoundError if absent."""
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user.id)
    )
    ips = result.scalar_one_or_none()
    if ips is None:
        raise NotFoundError(f"No IPS policy found for user {user.id}")
    return ips


async def _load_transactions(
    db: AsyncSession, user: User
) -> list[Transaction]:
    """Load all of the user's transactions (per-user isolated)."""
    result = await db.execute(
        select(Transaction).where(Transaction.user_id == user.id)
    )
    return list(result.scalars().all())


# --- Asset-class (BLOCK-eligible) checks ---------------------------------
def _asset_class_violations(symbol: str, ips: IpsRule) -> list[IpsViolation]:
    """Asset-class violations for a single ``symbol`` against the IPS flags.

    Precedence (deterministic): options first, then leverage, then the
    allowed-list check (a plain ticker outside ``allowed_symbols`` is a
    forbidden individual stock when ``no_individual_stocks`` is set). Each
    check is gated on its IPS flag and carries that rule's configured level.
    These are the only BLOCK-eligible rules.
    """
    normalized = _normalize_symbol(symbol)
    allowed = _parse_allowed_symbols(ips.allowed_symbols)
    violations: list[IpsViolation] = []

    if ips.no_options and is_option_symbol(normalized):
        violations.append(
            IpsViolation(
                rule_type=RULE_OPTIONS,
                level=ips.enforce_options,
                message=(
                    f"{normalized} is an options contract; the IPS forbids "
                    "options (NO_OPTIONS)."
                ),
                evidence={"symbol": normalized, "flag": "no_options"},
            )
        )
        return violations

    if ips.no_leverage and is_leverage_symbol(normalized):
        violations.append(
            IpsViolation(
                rule_type=RULE_LEVERAGE,
                level=ips.enforce_leverage,
                message=(
                    f"{normalized} is a leveraged instrument; the IPS forbids "
                    "leverage (NO_LEVERAGE)."
                ),
                evidence={"symbol": normalized, "flag": "no_leverage"},
            )
        )
        return violations

    if normalized not in allowed:
        # A non-allowed symbol is a forbidden asset. When no_individual_stocks
        # is set, a plain ticker outside the allowed list is specifically a
        # forbidden individual stock; the message reflects which flag applies.
        reason = (
            "an individual stock outside the allowed list "
            "(NO_INDIVIDUAL_STOCKS)"
            if ips.no_individual_stocks and is_plain_equity_symbol(normalized)
            else "outside the IPS allowed list"
        )
        violations.append(
            IpsViolation(
                rule_type=RULE_FORBIDDEN_ASSET,
                level=ips.enforce_forbidden_assets,
                message=(
                    f"{normalized} is {reason}. Allowed symbols: "
                    f"{', '.join(sorted(allowed)) or '(none)'}."
                ),
                evidence={
                    "symbol": normalized,
                    "allowed_symbols": sorted(allowed),
                    "no_individual_stocks": ips.no_individual_stocks,
                },
            )
        )
    return violations


def _min_holding_violation(
    symbol: str,
    sell_date: date,
    transactions: Sequence[Transaction],
    ips: IpsRule,
) -> IpsViolation | None:
    """WARN/INFO: a SELL of ``symbol`` within ``min_holding_period_years`` of
    its first recorded BUY. Behavioral — never BLOCK (clamped)."""
    normalized = _normalize_symbol(symbol)
    buy_dates = [
        txn.transaction_date
        for txn in transactions
        if txn.transaction_type == TransactionType.BUY.value
        and txn.asset_symbol
        and _normalize_symbol(txn.asset_symbol) == normalized
    ]
    if not buy_dates:
        return None
    first_buy = min(buy_dates)
    holding_period_ends = add_months(
        first_buy, ips.min_holding_period_years * 12
    )
    if sell_date >= holding_period_ends:
        return None
    return IpsViolation(
        rule_type=RULE_MIN_HOLDING,
        level=_clamp_behavioral(ips.enforce_min_holding),
        message=(
            f"Selling {normalized} on {sell_date.isoformat()} is within the "
            f"{ips.min_holding_period_years}-year minimum holding period "
            f"(first buy {first_buy.isoformat()}, holding ends "
            f"{holding_period_ends.isoformat()})."
        ),
        evidence={
            "symbol": normalized,
            "sell_date": sell_date.isoformat(),
            "first_buy_date": first_buy.isoformat(),
            "holding_period_ends": holding_period_ends.isoformat(),
            "min_holding_period_years": ips.min_holding_period_years,
        },
    )


# --- Per-order validation -------------------------------------------------
def _order_symbol_and_side(order: dict[str, Any]) -> tuple[str, str | None]:
    """Extract (symbol, side) from an order/recommendation mapping.

    Accepts ``symbol``/``asset_symbol``/``ticker`` for the symbol and
    ``side``/``type``/``transaction_type`` for the side (BUY/SELL/...).
    """
    symbol = (
        order.get("symbol")
        or order.get("asset_symbol")
        or order.get("ticker")
        or ""
    )
    side = order.get("side") or order.get("type") or order.get(
        "transaction_type"
    )
    return str(symbol), (str(side).upper() if side is not None else None)


def _validate_order(
    symbol: str,
    side: str | None,
    sell_date: date,
    ips: IpsRule,
    transactions: Sequence[Transaction],
) -> list[IpsViolation]:
    """All violations for one order (asset-class + min-holding on SELL)."""
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return []
    violations = _asset_class_violations(normalized, ips)
    if side == TransactionType.SELL.value:
        held = _min_holding_violation(
            normalized, sell_date, transactions, ips
        )
        if held is not None:
            violations.append(held)
    return violations


def _action_date(action: dict[str, Any]) -> date:
    """Resolve the effective date of an action (its ``date`` field or today)."""
    raw = action.get("date") or action.get("window_date")
    if raw is None:
        return kl_today()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValidationFailed(
            f"Invalid action date {raw!r}; expected ISO YYYY-MM-DD"
        ) from exc


def _verdict(
    violations: list[IpsViolation], override: bool
) -> EnforcementVerdict:
    """Assemble an :class:`EnforcementVerdict` from raw violations.

    Splits BLOCK-level (``violations``) from WARN/INFO (``warnings``);
    ``allowed`` is False iff a BLOCK exists and ``override`` is False.
    """
    block_value = IpsEnforcementLevel.BLOCK.value
    blocking = [v for v in violations if v.level == block_value]
    warnings = [v for v in violations if v.level != block_value]
    levels = [v.level for v in violations]
    allowed = not blocking or override
    return EnforcementVerdict(
        allowed=allowed,
        max_level=max_level(levels),
        violations=blocking,
        warnings=warnings,
    )


async def validate_action(
    db: AsyncSession,
    user: User,
    action: dict[str, Any],
    override: bool = False,
) -> EnforcementVerdict:
    """Validate an ``action`` against the user's IPS policy.

    ``action`` is a mapping with a ``kind`` of:

    * ``TRANSACTION`` — fields ``type`` (BUY/SELL/...), ``asset_symbol``,
      ``quantity``, ``date``. Only BUY/SELL carry a symbol that is checked;
      cash events (DEPOSIT/WITHDRAWAL/DIVIDEND/FEE) are never IPS-restricted.
    * ``EXECUTION_PLAN`` — ``orders``: a list of order mappings, each with a
      symbol and a side; every order is validated.
    * ``AI_RECOMMENDATION`` (Phase 6) — ``orders`` (or
      ``recommended_actions``): each recommended order is validated through
      the same gate.

    Returns an :class:`EnforcementVerdict`. ``allowed`` is False iff a
    ``BLOCK``-level violation exists and ``override`` is False. INFO/WARN
    violations never block — they surface as ``warnings``. This function only
    reads (it does not write the audit log); the enforcement point calls
    :func:`record_block_audit` on a blocked/overridden outcome.

    Raises :class:`ValidationFailed` on an unknown action kind and
    :class:`NotFoundError` when the user has no IPS policy.
    """
    ips = await _load_ips(db, user)
    kind = str(action.get("kind", ACTION_TRANSACTION)).upper()
    effective_date = _action_date(action)
    violations: list[IpsViolation] = []

    if kind == ACTION_TRANSACTION:
        txn_type = action.get("type") or action.get("transaction_type")
        side = str(txn_type).upper() if txn_type is not None else None
        if side in _TRADE_TYPES:
            transactions = await _load_transactions(db, user)
            symbol = (
                action.get("asset_symbol")
                or action.get("symbol")
                or action.get("ticker")
                or ""
            )
            violations.extend(
                _validate_order(
                    str(symbol),
                    side,
                    effective_date,
                    ips,
                    transactions,
                )
            )
    elif kind in (ACTION_EXECUTION_PLAN, ACTION_AI_RECOMMENDATION):
        orders = action.get("orders") or action.get("recommended_actions") or []
        if not isinstance(orders, (list, tuple)):
            raise ValidationFailed(
                f"{kind} action requires an 'orders' list"
            )
        transactions = await _load_transactions(db, user)
        for raw_order in orders:
            if not isinstance(raw_order, dict):
                raise ValidationFailed(
                    f"{kind} orders must be mappings with a symbol and side"
                )
            symbol, side = _order_symbol_and_side(raw_order)
            order_date = (
                _action_date(raw_order)
                if ("date" in raw_order or "window_date" in raw_order)
                else effective_date
            )
            violations.extend(
                _validate_order(
                    symbol, side, order_date, ips, transactions
                )
            )
    else:
        raise ValidationFailed(f"Unknown action kind {kind!r}")

    return _verdict(violations, override)


# --- Standing alerts + compliance ----------------------------------------
def _portfolio_violations(
    transactions: Sequence[Transaction],
    ips: IpsRule,
    prices: dict[str, Decimal] | None,
    fx_rate: Decimal | None,
) -> tuple[list[IpsViolation], bool]:
    """Standing (non-action) violations across the current portfolio.

    Covers held-symbol asset-class breaches, drift, and cash drag. Drift and
    cash drag require ``prices`` + ``fx_rate``; when either is missing those
    checks are skipped cleanly (the first element only carries asset-class
    violations then). Returns ``(violations, approaching)`` where
    ``approaching`` flags drift within the approaching band but below the
    threshold (only meaningful when priced).
    """
    state = replay(transactions)
    violations: list[IpsViolation] = []

    # Asset-class breaches on currently held symbols (e.g. a forbidden symbol
    # already in the ledger). De-duplicated per symbol.
    for symbol in sorted(state.positions):
        violations.extend(_asset_class_violations(symbol, ips))

    approaching = False
    if prices is not None and fx_rate is not None:
        current = valuation(state, prices, fx_rate)
        report = drift(current, ips)
        if not report.within_threshold:
            violations.append(
                IpsViolation(
                    rule_type=RULE_DRIFT,
                    level=_clamp_behavioral(ips.enforce_drift),
                    message=(
                        f"Allocation drift {report.max_abs_drift_pp}pp exceeds "
                        f"the {ips.drift_threshold_pct}pp IPS threshold."
                    ),
                    evidence={
                        "max_abs_drift_pp": str(report.max_abs_drift_pp),
                        "threshold_pp": str(ips.drift_threshold_pct),
                    },
                )
            )
        elif (
            report.max_abs_drift_pp
            > _APPROACHING_FRACTION * ips.drift_threshold_pct
        ):
            approaching = True
        if report.cash_drag_pp > 0:
            violations.append(
                IpsViolation(
                    rule_type=RULE_CASH_DRAG,
                    level=_clamp_behavioral(ips.enforce_cash_drag),
                    message=(
                        f"Cash drag {report.cash_drag_pp}pp above the "
                        f"{ips.max_cash_drag_pct}% policy maximum — idle cash "
                        "erodes long-term returns."
                    ),
                    evidence={
                        "cash_drag_pp": str(report.cash_drag_pp),
                        "max_cash_drag_pct": str(ips.max_cash_drag_pct),
                    },
                )
            )

    return violations, approaching


async def alerts(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
) -> list[IpsViolation]:
    """Return all standing IPS violations for ``user`` (most-severe first).

    Asset-class breaches are always evaluated; drift / cash-drag are included
    only when both ``prices`` and ``fx_rate`` are supplied. This is a pure
    read — persistence is the caller's job via :func:`record_block_audit`.
    """
    ips = await _load_ips(db, user)
    transactions = await _load_transactions(db, user)
    violations, _ = _portfolio_violations(transactions, ips, prices, fx_rate)
    return sorted(violations, key=lambda v: level_rank(v.level), reverse=True)


async def compute_compliance_score(
    db: AsyncSession,
    user: User,
    prices: dict[str, Decimal] | None = None,
    fx_rate: Decimal | None = None,
) -> int:
    """Compute the 0–100 IPS compliance score (DESIGN §19.5).

    Start at 100; subtract 15 per active BLOCK-level violation, 7 per WARN, 2
    per INFO, and 3 when drift is approaching (but still within) the
    threshold; clamp to [0, 100]. Drift / cash-drag contributions require
    ``prices`` + ``fx_rate``; without them those checks are skipped cleanly
    (only asset-class breaches then affect the score).
    """
    ips = await _load_ips(db, user)
    transactions = await _load_transactions(db, user)
    violations, approaching = _portfolio_violations(
        transactions, ips, prices, fx_rate
    )
    score = 100
    penalties = {
        IpsEnforcementLevel.BLOCK.value: _PENALTY_BLOCK,
        IpsEnforcementLevel.WARN.value: _PENALTY_WARN,
        IpsEnforcementLevel.INFO.value: _PENALTY_INFO,
    }
    for violation in violations:
        score -= penalties.get(violation.level, 0)
    if approaching:
        score -= _PENALTY_APPROACHING
    return max(0, min(100, score))


# --- Audit ----------------------------------------------------------------
async def record_block_audit(
    db: AsyncSession,
    user: User,
    violation: IpsViolation,
    override: bool,
) -> AuditLog:
    """Append a critical IPS_ALERT audit row for a BLOCK outcome or override.

    Writes ``AuditLog(event_type='IPS_ALERT', severity='CRITICAL')`` recording
    the violation and whether it was overridden. The row is added and flushed
    (assigning its id) but **not committed** — the request lifecycle owns the
    commit, matching ``services.behavior``. Returns the persisted row.
    """
    action = "IPS_BLOCK_OVERRIDDEN" if override else "IPS_BLOCK"
    description = (
        f"IPS BLOCK ({violation.rule_type}) "
        f"{'overridden' if override else 'rejected'}: {violation.message}"
    )
    row = AuditLog(
        user_id=user.id,
        event_type=AuditEventType.IPS_ALERT.value,
        action=action,
        severity=AuditSeverity.CRITICAL.value,
        entity="ips",
        entity_id=None,
        description=description,
        context=json.dumps(
            {
                "rule_type": violation.rule_type,
                "level": violation.level,
                "override": override,
                "evidence": violation.evidence,
            },
            default=str,
        ),
    )
    db.add(row)
    await db.flush()
    return row

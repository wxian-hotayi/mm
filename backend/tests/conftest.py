"""Shared fixtures for the WealthOS Phase 1 backend test suite.

Import-order discipline: ``app.core.config.get_settings`` caches Settings and
``app.db.session`` builds the async engine at import time, so the static
environment below is exported BEFORE any ``app`` module is imported.
``DATABASE_URL`` points at a per-session temporary SQLite file (created via
``tmp_path_factory``) and is exported inside the session-scoped ``app``
fixture — the first place an engine-touching ``app`` module is imported.
Test modules therefore import only engine-free modules (models, schemas,
services, utils) at module level.

Event loops: every async fixture and test is pinned to the session-scoped
event loop (``loop_scope="session"``) so aiosqlite connections pooled by the
shared engine are always used on the loop that created them.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.models.ips import IpsRule
    from app.models.transaction import Transaction
    from app.models.user import User

ADMIN_EMAIL = "admin@wealthos.test"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Adm1n-Pass-For-Tests-Only"
USER_PASSWORD = "User-Pass-For-Tests-Only"

os.environ["ENV"] = "development"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"
os.environ["ADMIN_EMAIL"] = ADMIN_EMAIL
os.environ["ADMIN_USERNAME"] = ADMIN_USERNAME
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
# Rate limiting is process-local and IP-keyed; the ASGI test transport has no
# client IP, so every test would share one bucket. Disable it globally and let
# the dedicated hardening suite re-enable it deterministically.
os.environ["RATE_LIMIT_ENABLED"] = "false"

API = "/api/v1"

# ---------------------------------------------------------------------------
# Canonical deterministic ledger (every FX rate is 4.45 USD->MYR).
#
# Hand-computed replay, asserted exactly in test_ledger_math.py:
#
# Date        Type      Detail                         Cash USD after  Notes
# 2026-01-05  DEPOSIT   RM8,900.00 @4.45 = $2,000.00   2,000.0000  deposits $2,000 / RM8,900
# 2026-02-03  DEPOSIT   RM4,450.00 @4.45 = $1,000.00   3,000.0000  deposits $3,000 / RM13,350
# 2026-02-10  BUY       2 VOO @ $470, fee $1           2,059.0000  cost 2x470+1 = $941.00;
#                                                                  VOO basis 941.00, avg 470.50
# 2026-02-24  BUY       1 QQQ @ $400, fee $1           1,658.0000  cost 401.00; QQQ basis
#                                                                  401.00, avg 401.00
# 2026-03-17  SELL      0.5 VOO @ $500, fee $1         1,907.0000  proceeds 0.5x500-1 = 249.00;
#                                                                  cost removed 0.5x470.50
#                                                                  = 235.25; realized
#                                                                  249-235.25 = 13.75
#                                                                  = 0.5x(500-470.50)-1;
#                                                                  VOO left 1.5 sh, basis
#                                                                  705.75, avg STILL 470.50
# 2026-03-20  DIVIDEND  VOO $3.30 (RM14.6850 @4.45)    1,910.3000  dividends 3.30
# 2026-03-31  FEE       $1.10 (RM4.8950 @4.45)         1,909.2000  fees 1.10
#
# Stored MYR totals for trades mirror the server derivation
# (quantity x unit_price_usd + fee_usd) x fx_rate_recorded:
#   BUY VOO  941.00 x 4.45 = 4,187.4500
#   BUY QQQ  401.00 x 4.45 = 1,784.4500
#   SELL VOO 251.00 x 4.45 = 1,116.9500
# ---------------------------------------------------------------------------

CANONICAL_FX = Decimal("4.4500")

CANONICAL_EXPECTED: dict[str, Decimal] = {
    "cash_usd": Decimal("1909.2000"),
    "voo_quantity": Decimal("1.5000"),
    "voo_avg_cost_usd": Decimal("470.5000"),
    "voo_cost_basis_usd": Decimal("705.7500"),
    "qqq_quantity": Decimal("1.0000"),
    "qqq_avg_cost_usd": Decimal("401.0000"),
    "qqq_cost_basis_usd": Decimal("401.0000"),
    "realized_gain_usd": Decimal("13.7500"),
    "dividends_usd": Decimal("3.3000"),
    "fees_usd": Decimal("1.1000"),
    "net_deposits_usd": Decimal("3000.0000"),
    "net_deposits_myr": Decimal("13350.0000"),
}


def make_transaction(**kwargs: object) -> "Transaction":
    """Build an in-memory Transaction row with safe defaults.

    Column-level Python defaults only apply at INSERT time, so ``fee_usd``
    and ``notes`` are defaulted explicitly for pure (non-persisted) replays.
    """
    from app.models.transaction import Transaction

    kwargs.setdefault("user_id", 1)
    kwargs.setdefault("fee_usd", Decimal("0.0000"))
    kwargs.setdefault("notes", "")
    return Transaction(**kwargs)


def canonical_transactions(user_id: int) -> list["Transaction"]:
    """The canonical deterministic ledger rows (see table above)."""
    return [
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 1, 5),
            transaction_type="DEPOSIT",
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("8900.0000"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 2, 3),
            transaction_type="DEPOSIT",
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("4450.0000"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 2, 10),
            transaction_type="BUY",
            asset_symbol="VOO",
            quantity=Decimal("2.0000"),
            unit_price_usd=Decimal("470.0000"),
            fee_usd=Decimal("1.0000"),
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("4187.4500"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 2, 24),
            transaction_type="BUY",
            asset_symbol="QQQ",
            quantity=Decimal("1.0000"),
            unit_price_usd=Decimal("400.0000"),
            fee_usd=Decimal("1.0000"),
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("1784.4500"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 3, 17),
            transaction_type="SELL",
            asset_symbol="VOO",
            quantity=Decimal("0.5000"),
            unit_price_usd=Decimal("500.0000"),
            fee_usd=Decimal("1.0000"),
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("1116.9500"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 3, 20),
            transaction_type="DIVIDEND",
            asset_symbol="VOO",
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("14.6850"),
        ),
        make_transaction(
            user_id=user_id,
            transaction_date=date(2026, 3, 31),
            transaction_type="FEE",
            fx_rate_recorded=CANONICAL_FX,
            total_amount_myr=Decimal("4.8950"),
        ),
    ]


@dataclass(frozen=True)
class SeededUser:
    """A user created directly in the database with a minted bearer token."""

    id: int
    email: str
    username: str
    password: str
    headers: dict[str, str]


@dataclass(frozen=True)
class SeededLedger:
    """The canonical ledger persisted for one isolated user."""

    user: SeededUser
    expected: dict[str, Decimal]
    transaction_ids: list[int]


class UserFactory(Protocol):
    """Async factory creating isolated users (each with a default IPS row)."""

    async def __call__(self, *, active: bool = True) -> SeededUser: ...


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app(tmp_path_factory: pytest.TempPathFactory) -> "FastAPI":
    """Configure DATABASE_URL, import the app and initialize the schema.

    ASGITransport does not run the lifespan, so ``init_db`` (create_all plus
    idempotent admin/IPS seed) is invoked manually here, once per session.
    """
    assert "app.db.session" not in sys.modules, (
        "app.db.session was imported before DATABASE_URL was configured; "
        "keep engine-touching app imports out of test module level"
    )
    db_path = tmp_path_factory.mktemp("wealthos") / "wealthos-test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.db.init_db import init_db
    from app.main import app as fastapi_app

    await init_db()
    return fastapi_app


@pytest_asyncio.fixture(loop_scope="session")
async def client(app: "FastAPI") -> AsyncIterator[AsyncClient]:
    """HTTP client speaking ASGI directly to the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth_headers(app: "FastAPI") -> dict[str, str]:
    """Bearer headers for the seeded admin, obtained via a real login."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        response = await async_client.post(
            f"{API}/auth/login",
            json={"identifier": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
    assert response.status_code == 200, response.text
    token = response.json()["token"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _user_password_hash(app: "FastAPI") -> str:
    """One bcrypt hash shared by all factory users (cost 12 is slow)."""
    from app.core.security import hash_password

    return hash_password(USER_PASSWORD)


@pytest_asyncio.fixture(loop_scope="session")
async def user_factory(app: "FastAPI", _user_password_hash: str) -> UserFactory:
    """Create isolated users directly in the database with minted tokens."""
    from app.core.security import create_access_token
    from app.db.session import SessionLocal
    from app.models import IpsRule, User
    from app.models.user import UserRole

    async def _create(*, active: bool = True) -> SeededUser:
        suffix = uuid.uuid4().hex[:12]
        email = f"user-{suffix}@wealthos.test"
        username = f"user-{suffix}"
        async with SessionLocal() as db:
            user = User(
                email=email,
                username=username,
                password_hash=_user_password_hash,
                role=UserRole.USER.value,
                base_currency="MYR",
                is_active=active,
            )
            db.add(user)
            await db.flush()
            db.add(IpsRule(user_id=user.id))
            await db.commit()
            user_id = user.id
        token = create_access_token(user_id, UserRole.USER.value)
        return SeededUser(
            id=user_id,
            email=email,
            username=username,
            password=USER_PASSWORD,
            headers={"Authorization": f"Bearer {token}"},
        )

    return _create


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_ledger(user_factory: UserFactory) -> SeededLedger:
    """Persist the canonical ledger for a fresh isolated user."""
    from app.db.session import SessionLocal

    user = await user_factory()
    rows = canonical_transactions(user.id)
    async with SessionLocal() as db:
        db.add_all(rows)
        await db.commit()
        transaction_ids = [row.id for row in rows]
    return SeededLedger(
        user=user,
        expected=dict(CANONICAL_EXPECTED),
        transaction_ids=transaction_ids,
    )


@pytest.fixture()
def canonical_ledger_rows() -> list["Transaction"]:
    """Unsaved canonical ledger rows for pure (database-free) replays."""
    return canonical_transactions(user_id=1)


@pytest.fixture()
def canonical_expected() -> dict[str, Decimal]:
    """Hand-computed expectations for the canonical ledger."""
    return dict(CANONICAL_EXPECTED)


@pytest.fixture()
def txn_builder() -> Callable[..., "Transaction"]:
    """In-memory Transaction builder for pure engine tests."""
    return make_transaction


@pytest.fixture()
def ips_factory() -> Callable[..., "IpsRule"]:
    """Build an in-memory IpsRule with the seeded defaults made explicit.

    Column-level defaults only apply at INSERT time, so every field the
    engines read is set explicitly here — including the Phase-2 three-tier
    enforcement levels (§19.5) and the unified execution-window engine config
    (§19.6). Keyword overrides win.
    """
    from app.models.ips import (
        DEFAULT_ALLOWED_SYMBOLS,
        DEFAULT_TARGET_WEIGHTS,
        IpsEnforcementLevel,
        IpsRule,
    )

    def _make(**overrides: object) -> IpsRule:
        params: dict[str, object] = {
            "user_id": 1,
            "target_weights": DEFAULT_TARGET_WEIGHTS,
            "drift_threshold_pct": Decimal("3.0"),
            "rebalance_frequency_months": 6,
            "min_holding_period_years": 10,
            "allowed_symbols": DEFAULT_ALLOWED_SYMBOLS,
            "no_individual_stocks": True,
            "no_options": True,
            "no_leverage": True,
            "max_cash_drag_pct": Decimal("5.0"),
            "is_active": True,
            # --- Phase-2 three-tier enforcement levels (§19.5) ---
            "enforce_forbidden_assets": IpsEnforcementLevel.BLOCK.value,
            "enforce_leverage": IpsEnforcementLevel.BLOCK.value,
            "enforce_options": IpsEnforcementLevel.BLOCK.value,
            "enforce_drift": IpsEnforcementLevel.WARN.value,
            "enforce_min_holding": IpsEnforcementLevel.WARN.value,
            "enforce_cash_drag": IpsEnforcementLevel.INFO.value,
            # --- Phase-2 execution-engine config (§19.5, §19.6) ---
            "min_deploy_threshold_myr": Decimal("1500"),
            "review_lead_days": 14,
            "execution_anchor_month": 3,
            "deployment_interval_months": 3,
            "rebalance_interval_months": 6,
            "execution_window_days": 21,
        }
        params.update(overrides)
        return IpsRule(**params)

    return _make


# ---------------------------------------------------------------------------
# Phase-2 service-layer fixtures: a live AsyncSession, a loaded User ORM
# object, a seeded cash account + movements, and a seeded IPS row.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def db_session(app: "FastAPI") -> "AsyncIterator[Any]":  # noqa: F821
    """An :class:`AsyncSession` bound to the shared test engine.

    Phase-2 services take ``(db, user, ...)`` and commit themselves, so tests
    drive them through a real session rather than the HTTP layer.
    """
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        yield session


class UserLoader(Protocol):
    """Async loader returning the persisted :class:`User` ORM object."""

    async def __call__(self, seeded: "SeededUser") -> "User": ...  # noqa: F821


@pytest_asyncio.fixture(loop_scope="session")
async def load_user(db_session: "Any") -> UserLoader:  # noqa: F821
    """Load the persisted :class:`User` ORM object for a :class:`SeededUser`.

    Phase-2 service functions expect a ``User`` model instance (they read
    ``user.id``); the user_factory only returns a lightweight ``SeededUser``.
    """
    from app.models.user import User

    async def _load(seeded: "SeededUser") -> "User":
        user = await db_session.get(User, seeded.id)
        assert user is not None, f"user {seeded.id} not found"
        return user

    return _load


@dataclass(frozen=True)
class SeededCash:
    """A user with two seeded operational cash accounts + movements.

    ``buffer_account_id`` is a buffer-source GXBank account; ``emergency_id``
    is a non-buffer emergency fund. The movement ledger is recorded so the
    derived buffer-source balance is exactly ``BUFFER_BALANCE_MYR`` and the
    emergency-fund balance is ``EMERGENCY_BALANCE_MYR``.
    """

    user: "SeededUser"
    buffer_account_id: int
    emergency_id: int
    buffer_balance_myr: Decimal
    emergency_balance_myr: Decimal


# Hand-computed seeded balances (see seeded_cash below).
#   Buffer (GXBank): +5,000 INFLOW + 50 INTEREST = RM5,050.00
#   Emergency fund : +10,000 INFLOW                = RM10,000.00
SEEDED_BUFFER_BALANCE_MYR = Decimal("5050.0000")
SEEDED_EMERGENCY_BALANCE_MYR = Decimal("10000.0000")


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_cash(user_factory: UserFactory) -> SeededCash:
    """Seed a buffer-source GXBank account and a non-buffer emergency fund.

    The buffer account carries no target buffer (its full balance is
    deployable); the emergency fund is excluded from deployable surplus by
    design (``is_buffer_source=False``).
    """
    from datetime import date as _date

    from app.db.session import SessionLocal
    from app.models.cash import (
        CashAccount,
        CashAccountType,
        CashMovement,
        CashMovementType,
    )

    user = await user_factory()
    async with SessionLocal() as db:
        buffer_account = CashAccount(
            user_id=user.id,
            name="GXBank",
            account_type=CashAccountType.GXBANK.value,
            currency="MYR",
            is_buffer_source=True,
            target_buffer_myr=Decimal("0.0000"),
            annual_interest_pct=Decimal("0.0000"),
            sort_order=0,
        )
        emergency = CashAccount(
            user_id=user.id,
            name="Emergency Fund",
            account_type=CashAccountType.EMERGENCY_FUND.value,
            currency="MYR",
            is_buffer_source=False,
            target_buffer_myr=Decimal("0.0000"),
            annual_interest_pct=Decimal("0.0000"),
            sort_order=1,
        )
        db.add_all([buffer_account, emergency])
        await db.flush()
        db.add_all(
            [
                CashMovement(
                    user_id=user.id,
                    account_id=buffer_account.id,
                    movement_date=_date(2026, 1, 5),
                    movement_type=CashMovementType.INFLOW.value,
                    amount_myr=Decimal("5000.0000"),
                ),
                CashMovement(
                    user_id=user.id,
                    account_id=buffer_account.id,
                    movement_date=_date(2026, 1, 31),
                    movement_type=CashMovementType.INTEREST.value,
                    amount_myr=Decimal("50.0000"),
                ),
                CashMovement(
                    user_id=user.id,
                    account_id=emergency.id,
                    movement_date=_date(2026, 1, 5),
                    movement_type=CashMovementType.INFLOW.value,
                    amount_myr=Decimal("10000.0000"),
                ),
            ]
        )
        await db.commit()
        buffer_id = buffer_account.id
        emergency_id = emergency.id
    return SeededCash(
        user=user,
        buffer_account_id=buffer_id,
        emergency_id=emergency_id,
        buffer_balance_myr=SEEDED_BUFFER_BALANCE_MYR,
        emergency_balance_myr=SEEDED_EMERGENCY_BALANCE_MYR,
    )

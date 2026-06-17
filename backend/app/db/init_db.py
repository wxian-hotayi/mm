"""Database initialization: create tables and idempotently seed core rows.

Seeds the admin user from ``ADMIN_EMAIL``/``ADMIN_USERNAME``/``ADMIN_PASSWORD``
and a default Investment Policy Statement for that admin. In production every
admin variable is required; in development safe defaults are applied.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import (
    CashAccount,
    CashAccountType,
    IpsEnforcementLevel,
    IpsRule,
    User,
    UserRole,
)

logger = get_logger("init_db")

_DEV_ADMIN_EMAIL = "admin@example.com"
_DEV_ADMIN_USERNAME = "admin"
_DEV_ADMIN_PASSWORD = "change-me-strong"


async def create_tables() -> None:
    """Create all tables registered on Base.metadata (no-op when present)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def resolve_admin_credentials(settings: Settings) -> tuple[str, str, str]:
    """Return (email, username, password) for the seed admin.

    Raises RuntimeError in production when any variable is missing; falls
    back to development defaults otherwise.
    """
    email = settings.ADMIN_EMAIL.strip()
    username = settings.ADMIN_USERNAME.strip()
    password = settings.ADMIN_PASSWORD.strip()
    missing = [
        name
        for name, value in (
            ("ADMIN_EMAIL", email),
            ("ADMIN_USERNAME", username),
            ("ADMIN_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        if settings.is_production:
            raise RuntimeError(
                "Cannot seed the admin user: required environment variables "
                f"are missing in production: {', '.join(missing)}. Set "
                "ADMIN_EMAIL, ADMIN_USERNAME and ADMIN_PASSWORD and retry."
            )
        email = email or _DEV_ADMIN_EMAIL
        username = username or _DEV_ADMIN_USERNAME
        password = password or _DEV_ADMIN_PASSWORD
        logger.warning(
            "Admin seed variables missing; using development defaults",
            extra={"missing": missing},
        )
    return email, username, password


async def ensure_admin(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    password: str,
    overwrite: bool = False,
) -> User:
    """Create the admin user if absent; otherwise ensure it stays an admin.

    With ``overwrite=True`` (CLI path) the email, username and password of an
    existing matching user are updated as well.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        result = await db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(password),
            role=UserRole.ADMIN.value,
            base_currency="MYR",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        logger.info(
            "Created admin user",
            extra={"email": email, "username": username, "user_id": user.id},
        )
        return user
    user.role = UserRole.ADMIN.value
    user.is_active = True
    if overwrite:
        user.email = email
        user.username = username
        user.password_hash = hash_password(password)
        logger.info(
            "Updated admin user credentials",
            extra={"email": email, "username": username, "user_id": user.id},
        )
    await db.flush()
    return user


async def ensure_default_ips(db: AsyncSession, user_id: int) -> IpsRule:
    """Create the default IPS policy row for a user if none exists.

    Populates the Phase-2 enforcement levels (§19.5) and the unified
    execution-window engine config (§19.6) with their canonical defaults.
    Idempotent: an existing policy row is returned unchanged.
    """
    result = await db.execute(
        select(IpsRule).where(IpsRule.user_id == user_id)
    )
    ips = result.scalar_one_or_none()
    if ips is not None:
        return ips
    ips = IpsRule(
        user_id=user_id,
        # Three-tier enforcement (§19.5): BLOCK only for forbidden assets.
        enforce_forbidden_assets=IpsEnforcementLevel.BLOCK.value,
        enforce_leverage=IpsEnforcementLevel.BLOCK.value,
        enforce_options=IpsEnforcementLevel.BLOCK.value,
        enforce_drift=IpsEnforcementLevel.WARN.value,
        enforce_min_holding=IpsEnforcementLevel.WARN.value,
        enforce_cash_drag=IpsEnforcementLevel.INFO.value,
        # Execution-engine config (§19.5, §19.6).
        min_deploy_threshold_myr=Decimal("1500"),
        review_lead_days=14,
        execution_anchor_month=3,
        deployment_interval_months=3,
        rebalance_interval_months=6,
        execution_window_days=21,
    )
    db.add(ips)
    await db.flush()
    logger.info(
        "Created default IPS policy",
        extra={"user_id": user_id, "ips_id": ips.id},
    )
    return ips


async def ensure_default_cash_account(
    db: AsyncSession, user_id: int
) -> CashAccount:
    """Seed one GXBank buffer-source cash account for a user if none exists.

    Idempotent: keyed on the existence of any cash account for the user so it
    runs safely on every startup (DESIGN §19.1).
    """
    result = await db.execute(
        select(CashAccount).where(CashAccount.user_id == user_id)
    )
    account = result.scalars().first()
    if account is not None:
        return account
    account = CashAccount(
        user_id=user_id,
        name="GXBank",
        account_type=CashAccountType.GXBANK.value,
        currency="MYR",
        is_buffer_source=True,
        target_buffer_myr=Decimal("0"),
        annual_interest_pct=Decimal("0"),
        sort_order=0,
        is_archived=False,
    )
    db.add(account)
    await db.flush()
    logger.info(
        "Created default cash account",
        extra={"user_id": user_id, "account_id": account.id},
    )
    return account


async def init_db() -> None:
    """Create the schema and seed the admin user plus default IPS policy."""
    setup_logging()
    settings = get_settings()
    await create_tables()
    email, username, password = resolve_admin_credentials(settings)
    async with SessionLocal() as db:
        admin = await ensure_admin(
            db, email=email, username=username, password=password
        )
        await ensure_default_ips(db, admin.id)
        await ensure_default_cash_account(db, admin.id)
        await db.commit()
    logger.info("Database initialization complete")

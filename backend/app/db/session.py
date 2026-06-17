"""Async engine and session factory (SQLite via aiosqlite, PostgreSQL-ready).

Relative SQLite file paths are anchored to the ``backend/`` directory so the
database lands in the same place whether the process starts from the
repository root or from ``backend/``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_BACKEND_DIR: Path = Path(__file__).resolve().parents[2]


def get_engine_url() -> URL:
    """Resolve the configured DATABASE_URL, anchoring relative SQLite paths."""
    url = make_url(get_settings().DATABASE_URL)
    if url.get_backend_name() != "sqlite":
        return url
    database = url.database
    if not database or database == ":memory:":
        return url
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = (_BACKEND_DIR / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=db_path.as_posix())


def _create_engine() -> AsyncEngine:
    url = get_engine_url()
    is_sqlite = url.get_backend_name() == "sqlite"
    connect_args: dict[str, Any] = (
        {"check_same_thread": False} if is_sqlite else {}
    )
    new_engine = create_async_engine(url, connect_args=connect_args)
    if is_sqlite:

        @event.listens_for(new_engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(
            dbapi_connection: Any, connection_record: Any
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return new_engine


engine: AsyncEngine = _create_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one AsyncSession per request."""
    async with SessionLocal() as session:
        yield session

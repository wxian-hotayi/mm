"""WealthOS application factory and uvicorn entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import API_VERSION
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.db.init_db import init_db

logger = get_logger("main")

_DESCRIPTION = (
    "Personal wealth operating system for a Malaysian long-term investor: "
    "ledger-first portfolio tracking (70% VOO / 30% QQQ), high-precision "
    "Decimal math, rebalance planning and behavior protection. "
    "Discipline beats intelligence."
)

_OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": "Login (JWT bearer) and current-user profile.",
    },
    {
        "name": "transactions",
        "description": (
            "Transaction ledger CRUD — the single source of truth; every "
            "mutation re-validates the full ledger."
        ),
    },
    {
        "name": "portfolio",
        "description": (
            "On-demand valuation and IPS-driven rebalance planning, derived "
            "from the ledger (no stored state)."
        ),
    },
    {
        "name": "analytics",
        "description": "Behavior-protection report and trade statistics.",
    },
    {
        "name": "health",
        "description": "Liveness probe with a real database round-trip.",
    },
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database (schema + idempotent seed) on startup."""
    await init_db()
    logger.info("WealthOS API started", extra={"version": API_VERSION})
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application: logging, CORS, error handlers, routes."""
    setup_logging()
    settings = get_settings()
    app = FastAPI(
        title="WealthOS API",
        version=API_VERSION,
        description=_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)

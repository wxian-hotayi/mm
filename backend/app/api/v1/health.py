"""Health endpoint with a live database round-trip (public, no auth)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.v1 import API_VERSION
from app.core.deps import DbDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: DbDep) -> dict[str, str]:
    """Liveness/readiness probe: executes ``SELECT 1`` against the database."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "version": API_VERSION, "db": "ok"}

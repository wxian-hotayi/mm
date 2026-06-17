"""Aggregated API v1 router (mounted under ``/api/v1`` in the app factory)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    action_status,
    analytics,
    auth,
    cash,
    cycle,
    deployment,
    execution,
    health,
    ips,
    networth,
    portfolio,
    transactions,
)

api_router = APIRouter()
# Phase 1 routers.
api_router.include_router(auth.router)
api_router.include_router(transactions.router)
api_router.include_router(portfolio.router)
api_router.include_router(analytics.router)
api_router.include_router(health.router)
# Phase 2 — Wealth Execution & Life-Cycle Domain (DESIGN §19.7).
api_router.include_router(cash.router)
api_router.include_router(deployment.router)
api_router.include_router(networth.router)
api_router.include_router(cycle.router)
api_router.include_router(action_status.router)
api_router.include_router(ips.router)
api_router.include_router(execution.router)

"""Behavior-protection report schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class BehaviorFlagOut(BaseModel):
    """Mirrors :class:`~app.services.behavior.BehaviorFlag`."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    severity: str
    title: str
    message: str
    evidence: dict[str, Any]


class TradeStatsOut(BaseModel):
    """Trade-frequency statistics over the trailing 30 days (KL calendar);
    ``max_trades_in_7d`` is the densest rolling 7-day window."""

    trades_30d: int
    buys_30d: int
    sells_30d: int
    max_trades_in_7d: int


class BehaviorHistoryOut(BaseModel):
    """One previously recorded behavior flag from the audit log."""

    code: str
    severity: str
    title: str
    message: str
    created_at: datetime


class BehaviorReportOut(BaseModel):
    """Full behavior report: live flags, trade statistics and the recent
    audit-log history of recorded flags."""

    flags: list[BehaviorFlagOut]
    trade_stats: TradeStatsOut
    recent_history: list[BehaviorHistoryOut]
    generated_at: datetime

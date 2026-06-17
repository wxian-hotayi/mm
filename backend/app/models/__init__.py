"""ORM models. Importing this package registers every table on Base.metadata."""

from app.db.base import Base
from app.models.audit import AuditEventType, AuditLog, AuditSeverity
from app.models.cash import (
    CashAccount,
    CashAccountType,
    CashMovement,
    CashMovementType,
)
from app.models.cycle import CycleStateLog, WealthCycleState
from app.models.deployment import (
    DeploymentIntent,
    DeploymentStatus,
    DeploymentTrigger,
)
from app.models.execution import (
    ExecutionPlan,
    ExecutionPlanKind,
    ExecutionPlanStatus,
)
from app.models.ips import (
    DEFAULT_ALLOWED_SYMBOLS,
    DEFAULT_TARGET_WEIGHTS,
    IpsEnforcementLevel,
    IpsRule,
)
from app.models.net_worth import NetWorthCategory, NetWorthEntry
from app.models.networth_snapshot import (
    NetWorthCashSnapshot,
    NetWorthCashSnapshotSource,
)
from app.models.transaction import Transaction, TransactionType
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "AuditEventType",
    "AuditLog",
    "AuditSeverity",
    "CashAccount",
    "CashAccountType",
    "CashMovement",
    "CashMovementType",
    "CycleStateLog",
    "WealthCycleState",
    "DeploymentIntent",
    "DeploymentStatus",
    "DeploymentTrigger",
    "ExecutionPlan",
    "ExecutionPlanKind",
    "ExecutionPlanStatus",
    "DEFAULT_ALLOWED_SYMBOLS",
    "DEFAULT_TARGET_WEIGHTS",
    "IpsEnforcementLevel",
    "IpsRule",
    "NetWorthCategory",
    "NetWorthEntry",
    "NetWorthCashSnapshot",
    "NetWorthCashSnapshotSource",
    "Transaction",
    "TransactionType",
    "User",
    "UserRole",
]

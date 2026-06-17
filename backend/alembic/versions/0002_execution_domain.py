"""execution domain: cash, deployment, execution, cycle, networth snapshots

Adds the Phase-2 Wealth Execution & Life-Cycle domain (DESIGN §19): the six new
tables (cash_accounts, cash_movements, deployment_intents, execution_plans,
cycle_state_log, net_worth_cash_snapshots), the new enforcement / execution-window
columns on ips_rules (§19.5, §19.6), and the audit_logs event_type CHECK change
that admits 'IPS_ALERT' (§19.5).

Money columns are sa.Numeric(18, 4) — the PostgreSQL-facing definition; the
runtime SQLite engine maps them to exact TEXT-backed decimals via
app.db.types.Money. SQLite schema changes (column adds, CHECK changes) use
batch_alter_table so they work on the runtime SQLite engine.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identity naming convention for batch CHECK changes: every CHECK name is used
# verbatim (no prefixing). All CHECK constraints touched here are therefore
# referenced by their full literal DB name (e.g. "ck_audit_logs_event_type"),
# matching both the names created by migration 0001 and the model metadata.
_IDENTITY_NAMING: dict[str, str] = {"ck": "%(constraint_name)s"}

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_AUDIT_EVENT_TYPES = (
    "event_type IN ('AUDIT', 'BEHAVIOR_FLAG', 'AUTH', 'IPS_ALERT')"
)
_OLD_AUDIT_EVENT_TYPES = (
    "event_type IN ('AUDIT', 'BEHAVIOR_FLAG', 'AUTH')"
)

# New ips_rules columns: (name, sa type, server default).
_IPS_ENFORCEMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("enforce_forbidden_assets", "'BLOCK'"),
    ("enforce_leverage", "'BLOCK'"),
    ("enforce_options", "'BLOCK'"),
    ("enforce_drift", "'WARN'"),
    ("enforce_min_holding", "'WARN'"),
    ("enforce_cash_drag", "'INFO'"),
)
_ENFORCEMENT_VALUES = "('INFO', 'WARN', 'BLOCK')"


def upgrade() -> None:
    # --- cash_accounts ---
    op.create_table(
        "cash_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'MYR'"),
            nullable=False,
        ),
        sa.Column(
            "is_buffer_source",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "target_buffer_myr",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "annual_interest_pct",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_type IN ('GXBANK', 'SAVINGS', 'EMERGENCY_FUND', "
            "'BUSINESS', 'BROKER_CASH_MYR', 'OTHER')",
            name="ck_cash_accounts_account_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_cash_accounts_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cash_accounts"),
    )

    # --- execution_plans (created before tables that FK to it) ---
    op.create_table(
        "execution_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("window_date", sa.Date(), nullable=False),
        sa.Column("plan_kind", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "cash_deployed_myr",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cash_deployed_usd",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("fx_rate_used", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "allocation_before",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "allocation_after",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "orders",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "steps",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "ips_compliant",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "ips_violations",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "plan_kind IN ('DEPLOY', 'REBALANCE', 'DEPLOY_AND_REBALANCE')",
            name="ck_execution_plans_plan_kind",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'EXECUTED', 'SKIPPED', "
            "'EXPIRED')",
            name="ck_execution_plans_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_execution_plans_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_plans"),
    )

    # --- cash_movements ---
    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("movement_date", sa.Date(), nullable=False),
        sa.Column("movement_type", sa.Text(), nullable=False),
        sa.Column("amount_myr", sa.Numeric(18, 4), nullable=False),
        sa.Column("counterparty_account_id", sa.Integer(), nullable=True),
        sa.Column("linked_transaction_id", sa.Integer(), nullable=True),
        sa.Column(
            "notes", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "movement_type IN ('INFLOW', 'OUTFLOW', 'INTEREST', "
            "'TRANSFER_OUT_TO_BROKER', 'TRANSFER_IN', 'ADJUSTMENT')",
            name="ck_cash_movements_movement_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_cash_movements_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["cash_accounts.id"],
            name="fk_cash_movements_account_id_cash_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_account_id"],
            ["cash_accounts.id"],
            name="fk_cash_movements_counterparty_account_id_cash_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_transaction_id"],
            ["transactions.id"],
            name="fk_cash_movements_linked_transaction_id_transactions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cash_movements"),
    )
    op.create_index(
        "ix_cash_movements_user_id_account_id_movement_date",
        "cash_movements",
        ["user_id", "account_id", "movement_date"],
    )

    # --- deployment_intents ---
    op.create_table(
        "deployment_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_account_id", sa.Integer(), nullable=True),
        sa.Column("amount_myr", sa.Numeric(18, 4), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'QUEUED'"),
            nullable=False,
        ),
        sa.Column("target_window_date", sa.Date(), nullable=True),
        sa.Column("execution_plan_id", sa.Integer(), nullable=True),
        sa.Column(
            "notes", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger IN ('THRESHOLD', 'MANUAL', 'WINDOW')",
            name="ck_deployment_intents_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'PLANNED', 'EXECUTED', 'CANCELLED')",
            name="ck_deployment_intents_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_deployment_intents_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_account_id"],
            ["cash_accounts.id"],
            name="fk_deployment_intents_source_account_id_cash_accounts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"],
            ["execution_plans.id"],
            name="fk_deployment_intents_execution_plan_id_execution_plans",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deployment_intents"),
    )

    # --- cycle_state_log ---
    op.create_table(
        "cycle_state_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "context",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('ACCUMULATION', 'READY_TO_DEPLOY', 'DEPLOYMENT', "
            "'REBALANCE_WINDOW')",
            name="ck_cycle_state_log_state",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_cycle_state_log_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cycle_state_log"),
    )
    op.create_index(
        "ix_cycle_state_log_user_id_entered_at",
        "cycle_state_log",
        ["user_id", "entered_at"],
    )

    # --- net_worth_cash_snapshots ---
    op.create_table(
        "net_worth_cash_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_cash_myr", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "breakdown",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'auto'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('auto', 'manual')",
            name="ck_net_worth_cash_snapshots_source",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_net_worth_cash_snapshots_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_net_worth_cash_snapshots"),
        sa.UniqueConstraint(
            "user_id",
            "snapshot_date",
            name="uq_net_worth_cash_snapshots_user_id",
        ),
    )

    # --- ips_rules: add enforcement + execution-window columns ---
    with op.batch_alter_table(
        "ips_rules", schema=None, naming_convention=_IDENTITY_NAMING
    ) as batch_op:
        for column_name, server_default in _IPS_ENFORCEMENT_COLUMNS:
            batch_op.add_column(
                sa.Column(
                    column_name,
                    sa.Text(),
                    server_default=sa.text(server_default),
                    nullable=False,
                )
            )
        batch_op.add_column(
            sa.Column(
                "min_deploy_threshold_myr",
                sa.Numeric(18, 4),
                server_default=sa.text("1500"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "review_lead_days",
                sa.Integer(),
                server_default=sa.text("14"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "execution_anchor_month",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "deployment_interval_months",
                sa.Integer(),
                server_default=sa.text("3"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "rebalance_interval_months",
                sa.Integer(),
                server_default=sa.text("6"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "execution_window_days",
                sa.Integer(),
                server_default=sa.text("21"),
                nullable=False,
            )
        )
        for column_name, _ in _IPS_ENFORCEMENT_COLUMNS:
            batch_op.create_check_constraint(
                f"ck_ips_rules_{column_name}",
                f"{column_name} IN {_ENFORCEMENT_VALUES}",
            )

    # --- audit_logs: extend event_type CHECK to admit IPS_ALERT ---
    with op.batch_alter_table(
        "audit_logs", schema=None, naming_convention=_IDENTITY_NAMING
    ) as batch_op:
        batch_op.drop_constraint("ck_audit_logs_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_logs_event_type",
            _NEW_AUDIT_EVENT_TYPES,
        )


def downgrade() -> None:
    # --- audit_logs: restore the original event_type CHECK ---
    with op.batch_alter_table(
        "audit_logs", schema=None, naming_convention=_IDENTITY_NAMING
    ) as batch_op:
        batch_op.drop_constraint("ck_audit_logs_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_logs_event_type",
            _OLD_AUDIT_EVENT_TYPES,
        )

    # --- ips_rules: drop the Phase-2 columns/constraints ---
    with op.batch_alter_table(
        "ips_rules", schema=None, naming_convention=_IDENTITY_NAMING
    ) as batch_op:
        for column_name, _ in _IPS_ENFORCEMENT_COLUMNS:
            batch_op.drop_constraint(
                f"ck_ips_rules_{column_name}", type_="check"
            )
        batch_op.drop_column("execution_window_days")
        batch_op.drop_column("rebalance_interval_months")
        batch_op.drop_column("deployment_interval_months")
        batch_op.drop_column("execution_anchor_month")
        batch_op.drop_column("review_lead_days")
        batch_op.drop_column("min_deploy_threshold_myr")
        for column_name, _ in reversed(_IPS_ENFORCEMENT_COLUMNS):
            batch_op.drop_column(column_name)

    op.drop_table("net_worth_cash_snapshots")
    op.drop_index(
        "ix_cycle_state_log_user_id_entered_at",
        table_name="cycle_state_log",
    )
    op.drop_table("cycle_state_log")
    op.drop_table("deployment_intents")
    op.drop_index(
        "ix_cash_movements_user_id_account_id_movement_date",
        table_name="cash_movements",
    )
    op.drop_table("cash_movements")
    op.drop_table("execution_plans")
    op.drop_table("cash_accounts")

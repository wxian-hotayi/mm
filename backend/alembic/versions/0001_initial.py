"""initial schema: users, transactions, net_worth_entries, ips_rules, audit_logs

Money columns are sa.Numeric(18, 4) — the PostgreSQL-facing definition; the
runtime SQLite engine maps the same columns to exact TEXT-backed decimals via
app.db.types.Money.

Revision ID: 0001
Revises:
Create Date: 2026-06-11

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role", sa.Text(), server_default=sa.text("'user'"), nullable=False
        ),
        sa.Column(
            "base_currency",
            sa.Text(),
            server_default=sa.text("'MYR'"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("transaction_type", sa.Text(), nullable=False),
        sa.Column("asset_symbol", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit_price_usd", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "fee_usd",
            sa.Numeric(18, 4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("fx_rate_recorded", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_amount_myr", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "notes", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("import_hash", sa.Text(), nullable=True),
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
            "transaction_type IN ('DEPOSIT', 'WITHDRAWAL', 'BUY', 'SELL', "
            "'DIVIDEND', 'FEE')",
            name="ck_transactions_transaction_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_transactions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint("import_hash", name="uq_transactions_import_hash"),
    )
    op.create_index(
        "ix_transactions_user_id_transaction_date",
        "transactions",
        ["user_id", "transaction_date"],
    )
    op.create_index(
        "ix_transactions_user_id_asset_symbol",
        "transactions",
        ["user_id", "asset_symbol"],
    )

    op.create_table(
        "net_worth_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("amount_myr", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "is_liability",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
            "category IN ('CASH', 'EMERGENCY_FUND', 'BUSINESS', 'SAVINGS', "
            "'OTHER_ASSET', 'LIABILITY')",
            name="ck_net_worth_entries_category",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_net_worth_entries_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_net_worth_entries"),
    )
    op.create_index(
        "ix_net_worth_entries_user_id_entry_date",
        "net_worth_entries",
        ["user_id", "entry_date"],
    )

    op.create_table(
        "ips_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_weights", sa.Text(), nullable=False),
        sa.Column(
            "drift_threshold_pct",
            sa.Numeric(18, 4),
            server_default=sa.text("3.0"),
            nullable=False,
        ),
        sa.Column(
            "rebalance_frequency_months",
            sa.Integer(),
            server_default=sa.text("6"),
            nullable=False,
        ),
        sa.Column(
            "min_holding_period_years",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column("allowed_symbols", sa.Text(), nullable=False),
        sa.Column(
            "no_individual_stocks",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "no_options",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "no_leverage",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "max_cash_drag_pct",
            sa.Numeric(18, 4),
            server_default=sa.text("5.0"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ips_rules_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ips_rules"),
        sa.UniqueConstraint("user_id", name="uq_ips_rules_user_id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Text(),
            server_default=sa.text("'INFO'"),
            nullable=False,
        ),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column(
            "description",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "context",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('AUDIT', 'BEHAVIOR_FLAG', 'AUTH')",
            name="ck_audit_logs_event_type",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name="ck_audit_logs_severity",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_audit_logs_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_user_id_event_type_created_at",
        "audit_logs",
        ["user_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_logs_user_id_event_type_created_at", table_name="audit_logs"
    )
    op.drop_table("audit_logs")
    op.drop_table("ips_rules")
    op.drop_index(
        "ix_net_worth_entries_user_id_entry_date",
        table_name="net_worth_entries",
    )
    op.drop_table("net_worth_entries")
    op.drop_index(
        "ix_transactions_user_id_asset_symbol", table_name="transactions"
    )
    op.drop_index(
        "ix_transactions_user_id_transaction_date", table_name="transactions"
    )
    op.drop_table("transactions")
    op.drop_table("users")

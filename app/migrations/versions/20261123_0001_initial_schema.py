"""initial schema: monitors, check_results, incidents

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+; no pgcrypto extension needed.
    op.create_table(
        "monitors",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False, server_default="GET"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("expected_status_min", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("expected_status_max", sa.Integer(), nullable=False, server_default="399"),
        sa.Column("keyword", sa.Text(), nullable=True),
        sa.Column("failure_threshold", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failing_since", TZ, nullable=True),
        sa.Column("failing_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", TZ, nullable=True),
        sa.Column("next_check_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("tls_expires_at", TZ, nullable=True),
        sa.Column("tls_checked_at", TZ, nullable=True),
        sa.Column("tls_notified_at", TZ, nullable=True),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="ck_monitors_name_len"),
        sa.CheckConstraint("char_length(url) <= 2048", name="ck_monitors_url_len"),
        sa.CheckConstraint("url ~* '^https?://'", name="ck_monitors_url_scheme"),
        sa.CheckConstraint("method IN ('GET', 'HEAD')", name="ck_monitors_method"),
        sa.CheckConstraint("interval_seconds BETWEEN 30 AND 3600", name="ck_monitors_interval"),
        sa.CheckConstraint("timeout_ms BETWEEN 1000 AND 30000", name="ck_monitors_timeout"),
        sa.CheckConstraint(
            "expected_status_min BETWEEN 100 AND 599 AND expected_status_max BETWEEN 100 AND 599 "
            "AND expected_status_min <= expected_status_max",
            name="ck_monitors_expected_status",
        ),
        sa.CheckConstraint(
            "failure_threshold BETWEEN 1 AND 10", name="ck_monitors_failure_threshold"
        ),
        sa.CheckConstraint(
            "status IN ('up', 'down', 'unknown', 'paused')", name="ck_monitors_status"
        ),
    )
    # Partial index for the scheduler's "due monitors" query.
    op.create_index(
        "ix_monitors_next_check_at_active",
        "monitors",
        ["next_check_at"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "check_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "monitor_id", UUID, sa.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("scheduled_for", TZ, nullable=False),
        sa.Column("checked_at", TZ, nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=False),
        # Idempotency: duplicate SQS deliveries can't create duplicate results.
        sa.UniqueConstraint("monitor_id", "scheduled_for", name="uq_check_results_monitor_sched"),
        sa.CheckConstraint(
            "error_type IS NULL OR error_type IN "
            "('timeout','dns','connection','tls','status','keyword','blocked','other')",
            name="ck_check_results_error_type",
        ),
    )
    op.create_index(
        "ix_check_results_monitor_checked",
        "check_results",
        ["monitor_id", sa.text("checked_at DESC")],
    )
    op.create_index("ix_check_results_checked_at", "check_results", ["checked_at"])

    op.create_table(
        "incidents",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "monitor_id", UUID, sa.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("resolved_at", TZ, nullable=True),
        sa.Column("first_error", sa.Text(), nullable=False),
        sa.Column("notified_open_at", TZ, nullable=True),
        sa.Column("notified_resolved_at", TZ, nullable=True),
    )
    # At most one open incident per monitor.
    op.create_index(
        "uq_incidents_one_open_per_monitor",
        "incidents",
        ["monitor_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_incidents_monitor_started", "incidents", ["monitor_id", sa.text("started_at DESC")]
    )


def downgrade() -> None:
    op.drop_table("incidents")
    op.drop_table("check_results")
    op.drop_table("monitors")

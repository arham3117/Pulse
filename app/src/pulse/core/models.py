"""SQLAlchemy 2.x models. The Alembic migration in ``migrations/versions`` is the source of
truth for the schema; these models mirror it (constraints are declared here too so that
``alembic check`` can detect drift)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONITOR_STATUSES = ("up", "down", "unknown", "paused")
ERROR_TYPES = ("timeout", "dns", "connection", "tls", "status", "keyword", "blocked", "other")


class Base(DeclarativeBase):
    pass


def _tz() -> DateTime:
    return DateTime(timezone=True)


class Monitor(Base):
    __tablename__ = "monitors"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="ck_monitors_name_len"),
        CheckConstraint("char_length(url) <= 2048", name="ck_monitors_url_len"),
        CheckConstraint("url ~* '^https?://'", name="ck_monitors_url_scheme"),
        CheckConstraint("method IN ('GET', 'HEAD')", name="ck_monitors_method"),
        CheckConstraint("interval_seconds BETWEEN 30 AND 3600", name="ck_monitors_interval"),
        CheckConstraint("timeout_ms BETWEEN 1000 AND 30000", name="ck_monitors_timeout"),
        CheckConstraint(
            "expected_status_min BETWEEN 100 AND 599 AND expected_status_max BETWEEN 100 AND 599 "
            "AND expected_status_min <= expected_status_max",
            name="ck_monitors_expected_status",
        ),
        CheckConstraint("failure_threshold BETWEEN 1 AND 10", name="ck_monitors_failure_threshold"),
        CheckConstraint("status IN ('up', 'down', 'unknown', 'paused')", name="ck_monitors_status"),
        Index(
            "ix_monitors_next_check_at_active",
            "next_check_at",
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text, server_default="GET")
    interval_seconds: Mapped[int] = mapped_column(Integer, server_default="60")
    timeout_ms: Mapped[int] = mapped_column(Integer, server_default="5000")
    expected_status_min: Mapped[int] = mapped_column(Integer, server_default="200")
    expected_status_max: Mapped[int] = mapped_column(Integer, server_default="399")
    keyword: Mapped[str | None] = mapped_column(Text)
    failure_threshold: Mapped[int] = mapped_column(Integer, server_default="3")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_public: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, server_default="unknown")
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    # Not in the original plan table: remember when/why the current failure streak began so
    # the incident can record "started_at = first failed check" without scanning results.
    failing_since: Mapped[datetime | None] = mapped_column(_tz())
    failing_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(_tz())
    next_check_at: Mapped[datetime] = mapped_column(_tz(), server_default=func.now())
    tls_expires_at: Mapped[datetime | None] = mapped_column(_tz())
    tls_checked_at: Mapped[datetime | None] = mapped_column(_tz())
    tls_notified_at: Mapped[datetime | None] = mapped_column(_tz())
    created_at: Mapped[datetime] = mapped_column(_tz(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _tz(), server_default=func.now(), onupdate=func.now()
    )


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        # Idempotency: a redelivered SQS message can't create a second result.
        UniqueConstraint("monitor_id", "scheduled_for", name="uq_check_results_monitor_sched"),
        Index("ix_check_results_monitor_checked", "monitor_id", text("checked_at DESC")),
        Index("ix_check_results_checked_at", "checked_at"),  # retention cleanup + staleness
        CheckConstraint(
            "error_type IS NULL OR error_type IN "
            "('timeout','dns','connection','tls','status','keyword','blocked','other')",
            name="ck_check_results_error_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitors.id", ondelete="CASCADE")
    )
    scheduled_for: Mapped[datetime] = mapped_column(_tz())
    checked_at: Mapped[datetime] = mapped_column(_tz())
    success: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str] = mapped_column(Text)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        # At most one open incident per monitor.
        Index(
            "uq_incidents_one_open_per_monitor",
            "monitor_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_incidents_monitor_started", "monitor_id", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    monitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitors.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(_tz())
    resolved_at: Mapped[datetime | None] = mapped_column(_tz())
    first_error: Mapped[str] = mapped_column(Text)
    notified_open_at: Mapped[datetime | None] = mapped_column(_tz())
    notified_resolved_at: Mapped[datetime | None] = mapped_column(_tz())

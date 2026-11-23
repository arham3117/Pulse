"""Prometheus metrics, one registry per process type.

Separate registries mean the API's ``/metrics`` doesn't export (always-zero) worker series,
which would otherwise confuse alerts such as ``PulseChecksStalled``.

Cardinality rule: labels only take values from small fixed sets (method, route template,
status code, result, error_type, queue, state, kind). Never monitor_id or URL.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import REGISTRY as DEFAULT_REGISTRY  # process + platform + gc

API_REGISTRY = CollectorRegistry(auto_describe=True)
SCHEDULER_REGISTRY = CollectorRegistry(auto_describe=True)
WORKER_REGISTRY = CollectorRegistry(auto_describe=True)

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_CHECK_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30)
# Includes 90 exactly: the "check freshness" SLI counts checks with lag <= 90s.
_LAG_BUCKETS = (1, 5, 10, 15, 30, 45, 60, 90, 120, 180, 300, 600)

# --- API ---------------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "pulse_http_requests_total",
    "HTTP requests handled by the API.",
    ["method", "route", "status"],
    registry=API_REGISTRY,
)
HTTP_DURATION = Histogram(
    "pulse_http_request_duration_seconds",
    "API request latency by route template.",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
    registry=API_REGISTRY,
)

# --- Scheduler ---------------------------------------------------------------------------
SCHED_ENQUEUED = Counter(
    "pulse_scheduler_checks_enqueued_total",
    "Check messages sent to SQS.",
    registry=SCHEDULER_REGISTRY,
)
SCHED_LOOP_DURATION = Histogram(
    "pulse_scheduler_loop_duration_seconds",
    "Duration of one scheduler loop.",
    buckets=_LATENCY_BUCKETS,
    registry=SCHEDULER_REGISTRY,
)
SCHED_DUE = Gauge(
    "pulse_scheduler_due_monitors",
    "Monitors claimed in the last scheduler loop.",
    registry=SCHEDULER_REGISTRY,
)
SCHED_LAG = Gauge(
    "pulse_scheduler_lag_seconds",
    "How overdue the oldest due monitor was at the start of the last loop.",
    registry=SCHEDULER_REGISTRY,
)
QUEUE_MESSAGES = Gauge(
    "pulse_queue_messages",
    "Approximate SQS message counts.",
    ["queue", "state"],
    registry=SCHEDULER_REGISTRY,
)
ACTIVE_MONITORS = Gauge(
    "pulse_active_monitors",
    "Number of active (not paused) monitors.",
    registry=SCHEDULER_REGISTRY,
)

# --- Worker ------------------------------------------------------------------------------
CHECKS = Counter(
    "pulse_checks_total",
    "Checks executed, by result and error type (error_type='none' on success).",
    ["result", "error_type"],
    registry=WORKER_REGISTRY,
)
CHECK_DURATION = Histogram(
    "pulse_check_duration_seconds",
    "Wall time of the HTTP part of a check.",
    buckets=_CHECK_BUCKETS,
    registry=WORKER_REGISTRY,
)
CHECK_SCHEDULE_LAG = Histogram(
    "pulse_check_schedule_lag_seconds",
    "checked_at minus scheduled_for.",
    buckets=_LAG_BUCKETS,
    registry=WORKER_REGISTRY,
)
WORKER_INFLIGHT = Gauge(
    "pulse_worker_inflight_checks",
    "Checks currently running in this worker.",
    registry=WORKER_REGISTRY,
)
NOTIFICATIONS = Counter(
    "pulse_notifications_total",
    "Notifications sent, by kind and result.",
    ["kind", "result"],
    registry=WORKER_REGISTRY,
)
INCIDENTS_OPENED = Counter(
    "pulse_incidents_opened_total",
    "Incidents opened.",
    registry=WORKER_REGISTRY,
)
WORKER_MESSAGE_ERRORS = Counter(
    "pulse_worker_message_errors_total",
    "Messages that raised an unhandled error (left on the queue for redelivery).",
    registry=WORKER_REGISTRY,
)


def render(registry: CollectorRegistry) -> tuple[bytes, str]:
    """Text exposition of ``registry`` plus process/GC metrics from the default registry."""
    return generate_latest(registry) + generate_latest(DEFAULT_REGISTRY), CONTENT_TYPE_LATEST

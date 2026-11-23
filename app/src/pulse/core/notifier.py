"""Product notifications (incident opened / resolved / TLS expiring).

``build_notifier`` picks the implementation from the webhook URL host:
  * ``hooks.slack.com``                    → SlackWebhookNotifier
  * ``discord.com`` / ``discordapp.com``   → DiscordWebhookNotifier
  * no webhook configured                  → LogNotifier (local default)

Delivery is retried 3 times with exponential backoff. Notifications are sent *after* the
DB commit, so a crash between commit and send can lose one; a transactional outbox is a
listed stretch goal (plan, Phase 10).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

import httpx

from pulse.core.logging import get_logger, redact_url
from pulse.core.metrics import NOTIFICATIONS

log = get_logger()


class EventKind(StrEnum):
    INCIDENT_OPENED = "incident_opened"
    INCIDENT_RESOLVED = "incident_resolved"
    TLS_EXPIRING = "tls_expiring"


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    kind: EventKind
    monitor_name: str
    monitor_url: str
    occurred_at: datetime
    error: str | None = None
    consecutive_failures: int | None = None
    started_at: datetime | None = None
    downtime: timedelta | None = None
    tls_expires_at: datetime | None = None


def format_duration(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = [f"{days}d"] if days else []
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    if not days:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_message(event: NotificationEvent) -> str:
    """Human-readable text used by every notifier. URLs are shown without query strings."""
    url = redact_url(event.monitor_url)
    if event.kind is EventKind.INCIDENT_OPENED:
        since = event.started_at.strftime("%H:%M UTC") if event.started_at else "unknown"
        return (
            f"🔴 DOWN: {event.monitor_name} ({url}) — {event.error or 'check failed'}. "
            f"{event.consecutive_failures} consecutive failures since {since}."
        )
    if event.kind is EventKind.INCIDENT_RESOLVED:
        down = format_duration(event.downtime) if event.downtime else "unknown"
        return f"✅ RECOVERED: {event.monitor_name} — down for {down}."
    expires = event.tls_expires_at
    days = (expires - event.occurred_at).days if expires else None
    when = expires.strftime("%Y-%m-%d") if expires else "unknown"
    return (
        f"⚠️ TLS EXPIRING: {event.monitor_name} ({url}) — certificate expires {when} "
        f"(in {days} days)."
    )


class Notifier(ABC):
    name: str = "base"

    async def notify(self, event: NotificationEvent) -> bool:
        """Deliver with retries. Never raises; returns success and records metrics."""
        delay = 1.0
        for attempt in range(1, 4):
            try:
                await self._send(event)
                NOTIFICATIONS.labels(kind=event.kind.value, result="success").inc()
                return True
            except Exception as exc:
                log.warning(
                    "notification_failed",
                    notifier=self.name,
                    kind=event.kind.value,
                    attempt=attempt,
                    error=type(exc).__name__,
                )
                if attempt < 3:
                    await asyncio.sleep(delay)
                    delay *= 2
        NOTIFICATIONS.labels(kind=event.kind.value, result="failure").inc()
        return False

    @abstractmethod
    async def _send(self, event: NotificationEvent) -> None: ...

    async def aclose(self) -> None:
        return None


class LogNotifier(Notifier):
    name = "log"

    async def _send(self, event: NotificationEvent) -> None:
        log.info("notification", kind=event.kind.value, message=format_message(event))


class _WebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = webhook_url
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    def _payload(self, text: str) -> dict[str, object]:
        raise NotImplementedError

    async def _send(self, event: NotificationEvent) -> None:
        resp = await self._client.post(self._url, json=self._payload(format_message(event)))
        resp.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class SlackWebhookNotifier(_WebhookNotifier):
    name = "slack"

    def _payload(self, text: str) -> dict[str, object]:
        return {"text": text}


class DiscordWebhookNotifier(_WebhookNotifier):
    name = "discord"

    def _payload(self, text: str) -> dict[str, object]:
        # Don't let monitor names ping @everyone.
        return {"content": text, "allowed_mentions": {"parse": []}}


def build_notifier(webhook_url: str | None) -> Notifier:
    if not webhook_url:
        return LogNotifier()
    host = (urlsplit(webhook_url).hostname or "").lower()
    if host == "hooks.slack.com":
        return SlackWebhookNotifier(webhook_url)
    if host in ("discord.com", "discordapp.com") or host.endswith(".discord.com"):
        return DiscordWebhookNotifier(webhook_url)
    raise ValueError("PULSE_ALERT_WEBHOOK_URL must be a Slack or Discord incoming webhook URL")

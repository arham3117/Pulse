"""Thin async wrapper around SQS (aioboto3). Works against AWS or ElasticMQ locally.

On AWS no credentials are configured anywhere: boto's default chain picks up the EKS Pod
Identity credentials injected into the pod. Locally ElasticMQ accepts any credentials.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aioboto3
from botocore.config import Config

from pulse.core.config import Settings

SEND_BATCH_MAX = 10  # SQS SendMessageBatch limit


@dataclass(frozen=True, slots=True)
class CheckMessage:
    monitor_id: uuid.UUID
    scheduled_for: datetime

    def to_body(self) -> str:
        return json.dumps(
            {"monitor_id": str(self.monitor_id), "scheduled_for": self.scheduled_for.isoformat()}
        )

    @classmethod
    def from_body(cls, body: str) -> CheckMessage:
        """Raises ``ValueError``/``KeyError``/``TypeError`` for malformed (poison) messages."""
        data = json.loads(body)
        scheduled = datetime.fromisoformat(data["scheduled_for"])
        if scheduled.tzinfo is None:
            raise ValueError("scheduled_for must be timezone-aware")
        return cls(monitor_id=uuid.UUID(data["monitor_id"]), scheduled_for=scheduled)


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    body: str
    receipt_handle: str
    message_id: str
    receive_count: int


class SQSQueue:
    """Long-lived SQS client. Use as ``async with SQSQueue(settings) as q:``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = AsyncExitStack()
        self._client: Any = None

    async def __aenter__(self) -> SQSQueue:
        kwargs: dict[str, Any] = {
            "region_name": self._settings.aws_region,
            "config": Config(retries={"mode": "standard", "max_attempts": 5}),
        }
        if self._settings.sqs_endpoint_url:
            kwargs["endpoint_url"] = self._settings.sqs_endpoint_url
            # ElasticMQ doesn't check credentials, but botocore insists on having some.
            if not os.environ.get("AWS_ACCESS_KEY_ID"):
                kwargs["aws_access_key_id"] = "local"
                kwargs["aws_secret_access_key"] = "local"  # noqa: S105 - not a secret
        session = aioboto3.Session()
        self._client = await self._stack.enter_async_context(session.client("sqs", **kwargs))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    @property
    def queue_url(self) -> str:
        return self._settings.sqs_queue_url

    async def send_checks(self, messages: list[CheckMessage]) -> list[CheckMessage]:
        """Send in batches of 10. Returns the messages that SQS reported as failed."""
        failed: list[CheckMessage] = []
        for start in range(0, len(messages), SEND_BATCH_MAX):
            chunk = messages[start : start + SEND_BATCH_MAX]
            entries = [{"Id": str(i), "MessageBody": m.to_body()} for i, m in enumerate(chunk)]
            resp = await self._client.send_message_batch(QueueUrl=self.queue_url, Entries=entries)
            failed.extend(chunk[int(f["Id"])] for f in resp.get("Failed", []))
        return failed

    async def receive(
        self, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[ReceivedMessage]:
        resp = await self._client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return [
            ReceivedMessage(
                body=m["Body"],
                receipt_handle=m["ReceiptHandle"],
                message_id=m["MessageId"],
                receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1)),
            )
            for m in resp.get("Messages", [])
        ]

    async def delete(self, receipt_handle: str) -> None:
        await self._client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)

    async def depth(self, queue_url: str) -> tuple[int, int]:
        """(visible, in-flight) approximate message counts for ``queue_url``."""
        resp = await self._client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )
        attrs = resp.get("Attributes", {})
        return (
            int(attrs.get("ApproximateNumberOfMessages", 0)),
            int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        )

    async def ping(self) -> bool:
        try:
            await self.depth(self.queue_url)
            return True
        except Exception:
            return False

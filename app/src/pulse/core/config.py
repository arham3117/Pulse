"""Process configuration, read only from ``PULSE_*`` environment variables (12-factor).

Every process (api, scheduler, worker, target, maintenance) uses the same ``Settings``
class; each one only reads the fields it needs. Secrets are ``SecretStr`` so they never
show up in reprs or logs by accident.
"""

from __future__ import annotations

import socket
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

Env = Literal["local", "test", "dev", "prod"]
SslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSE_", case_sensitive=False, extra="ignore")

    env: Env = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database
    db_host: str = "localhost"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "pulse"
    db_user: str = "postgres"
    db_password: SecretStr = SecretStr("")
    db_sslmode: SslMode = "disable"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)

    # AWS / SQS
    aws_region: str = "us-east-2"
    sqs_queue_url: str = ""
    sqs_dlq_url: str = ""
    sqs_endpoint_url: str | None = None  # local only (ElasticMQ); unset on AWS

    # Secrets used by api/worker
    admin_api_key: SecretStr = SecretStr("")
    alert_webhook_url: SecretStr = SecretStr("")

    # SSRF exemptions: hostnames allowed to resolve to private IPs (chaos target only)
    allow_private_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Worker
    worker_concurrency: int = Field(default=50, ge=1, le=1000)
    worker_id: str = Field(default_factory=socket.gethostname)

    # Scheduler
    scheduler_interval_seconds: float = Field(default=5, gt=0, le=300)
    scheduler_batch_size: int = Field(default=500, ge=1, le=10_000)

    # Maintenance
    retention_days: int = Field(default=30, ge=1, le=3650)

    # Chaos drill (Phase 8): makes /readyz always fail
    chaos_break_readiness: bool = False

    # Listen ports. Deliberately NOT named PULSE_API_PORT / PULSE_TARGET_PORT: Kubernetes
    # "service links" inject <SERVICE>_PORT=tcp://ip:port for every Service in the namespace,
    # so Services named pulse-api / pulse-target would collide with those names.
    api_listen_port: int = 8000
    health_listen_port: int = 9100
    target_listen_port: int = 8080

    @field_validator("allow_private_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [h.strip().lower() for h in value.split(",") if h.strip()]
        return value

    @field_validator("sqs_endpoint_url", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def db_url(self) -> URL:
        """Async SQLAlchemy URL built from the ``PULSE_DB_*`` parts.

        ``URL.create`` escapes the password correctly, so special characters in an
        RDS-generated password can't break the connection string.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value() or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def db_connect_args(self) -> dict[str, object]:
        # asyncpg takes ``ssl`` rather than ``sslmode``; it accepts libpq mode names.
        return {"ssl": self.db_sslmode} if self.db_sslmode != "disable" else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()

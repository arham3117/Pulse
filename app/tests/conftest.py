"""Shared fixtures.

Integration tests need Postgres + ElasticMQ (``docker compose up -d postgres elasticmq``,
or the service containers in CI). They run against a separate ``pulse_test`` database so
they never wipe local dev data, and each test gets its own fresh SQS queue.
If Postgres isn't reachable, integration tests are skipped with a clear reason (in CI the
services always exist, so they always run there).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import aioboto3
import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pulse.core.config import Settings
from pulse.core.db import create_engine, create_sessionmaker

APP_DIR = Path(__file__).resolve().parent.parent
TEST_DB = "pulse_test"
ADMIN_KEY = "test-admin-key"


def _base_env() -> dict[str, str]:
    return {
        "PULSE_ENV": "test",
        "PULSE_DB_HOST": os.environ.get("PULSE_DB_HOST", "localhost"),
        "PULSE_DB_PORT": os.environ.get("PULSE_DB_PORT", "5433"),
        "PULSE_DB_USER": os.environ.get("PULSE_DB_USER", "postgres"),
        "PULSE_DB_PASSWORD": os.environ.get("PULSE_DB_PASSWORD", "postgres"),
        "PULSE_DB_SSLMODE": "disable",
        "PULSE_SQS_ENDPOINT_URL": os.environ.get("PULSE_SQS_ENDPOINT_URL", "http://localhost:9324"),
        "PULSE_AWS_REGION": os.environ.get("PULSE_AWS_REGION", "us-east-1"),
    }


def make_settings(**overrides: object) -> Settings:
    env = _base_env()
    values: dict[str, object] = {
        "env": "test",
        "db_host": env["PULSE_DB_HOST"],
        "db_port": int(env["PULSE_DB_PORT"]),
        "db_name": TEST_DB,
        "db_user": env["PULSE_DB_USER"],
        "db_password": env["PULSE_DB_PASSWORD"],
        "db_sslmode": "disable",
        "sqs_endpoint_url": env["PULSE_SQS_ENDPOINT_URL"],
        "aws_region": env["PULSE_AWS_REGION"],
        "admin_api_key": ADMIN_KEY,
        "allow_private_hosts": ["localhost"],
        "worker_id": "test-worker",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def _prepare_database() -> str | None:
    env = _base_env()
    try:
        conn = await asyncpg.connect(
            host=env["PULSE_DB_HOST"],
            port=int(env["PULSE_DB_PORT"]),
            user=env["PULSE_DB_USER"],
            password=env["PULSE_DB_PASSWORD"],
            database="postgres",
            timeout=3,
        )
    except Exception as exc:
        return f"Postgres not reachable at {env['PULSE_DB_HOST']}:{env['PULSE_DB_PORT']} ({exc})"
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()
    return None


@pytest.fixture(scope="session")
def database() -> None:
    """Create a fresh pulse_test database and migrate it with the real Alembic migration."""
    skip_reason = asyncio.run(_prepare_database())
    if skip_reason:
        pytest.skip(skip_reason)
    env = {**os.environ, **_base_env(), "PULSE_DB_NAME": TEST_DB}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=APP_DIR,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
async def engine(database: None, settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(settings)
    async with eng.begin() as conn:
        await conn.execute(text("TRUNCATE incidents, check_results, monitors CASCADE"))
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(engine)


@pytest.fixture
async def sqs_queue_urls(settings: Settings) -> AsyncIterator[tuple[str, str]]:
    """A fresh queue + DLQ (maxReceiveCount=3) in ElasticMQ for this test."""
    name = f"test-{uuid.uuid4().hex[:12]}"
    session = aioboto3.Session()
    async with session.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    ) as client:
        try:
            dlq = (await client.create_queue(QueueName=f"{name}-dlq"))["QueueUrl"]
        except Exception as exc:
            pytest.skip(f"ElasticMQ not reachable at {settings.sqs_endpoint_url} ({exc})")
        dlq_arn = (await client.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"]))[
            "Attributes"
        ]["QueueArn"]
        main = (
            await client.create_queue(
                QueueName=name,
                Attributes={
                    "VisibilityTimeout": "1",
                    "RedrivePolicy": f'{{"deadLetterTargetArn":"{dlq_arn}","maxReceiveCount":"3"}}',
                },
            )
        )["QueueUrl"]
        yield main, dlq
        for url in (main, dlq):
            await client.delete_queue(QueueUrl=url)

"""Run the real Scheduler.run() and Worker.run() loops (health servers, SQS long-polling,
graceful shutdown) against Postgres + ElasticMQ + the chaos target."""

import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from pulse.core.models import CheckResult, Monitor
from pulse.scheduler.main import Scheduler
from pulse.worker.main import Worker
from tests.integration.test_end_to_end import target_url  # noqa: F401 - fixture

pytestmark = pytest.mark.integration


async def test_scheduler_and_worker_processes(
    engine,
    sessionmaker,
    settings,
    sqs_queue_urls,
    target_url,  # noqa: F811
) -> None:
    main_url, dlq_url = sqs_queue_urls
    settings = settings.model_copy(
        update={
            "sqs_queue_url": main_url,
            "sqs_dlq_url": dlq_url,
            "scheduler_interval_seconds": 0.2,
            "health_listen_port": 0,
        }
    )
    async with sessionmaker() as s, s.begin():
        s.add_all(Monitor(name=f"p{i}", url=f"{target_url}/ok") for i in range(3))

    scheduler, worker = Scheduler(settings), Worker(settings)
    tasks = [asyncio.create_task(scheduler.run()), asyncio.create_task(worker.run())]
    try:
        async with asyncio.timeout(20):
            while True:
                async with sessionmaker() as s:
                    done = await s.scalar(select(func.count()).select_from(CheckResult))
                if done >= 3:
                    break
                await asyncio.sleep(0.2)
    finally:
        scheduler.stopping.set()
        worker.stopping.set()
        # Graceful shutdown must not wait for the 20s SQS long poll.
        async with asyncio.timeout(10):
            await asyncio.gather(*tasks)

    async with sessionmaker() as s:
        statuses = (await s.scalars(select(Monitor.status))).all()
    assert statuses == ["up", "up", "up"]


async def test_worker_health_server_serves_metrics(engine, settings, sqs_queue_urls) -> None:
    main_url, _ = sqs_queue_urls
    settings = settings.model_copy(update={"sqs_queue_url": main_url, "health_listen_port": 0})
    worker = Worker(settings)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.5)
    try:
        assert worker.health is not None
        port = worker.health.port  # port 0 = OS picked a free port
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            assert (await c.get("/readyz")).status_code == 200
            assert "pulse_worker_inflight_checks" in (await c.get("/metrics")).text
    finally:
        worker.stopping.set()
        await asyncio.wait_for(task, 10)

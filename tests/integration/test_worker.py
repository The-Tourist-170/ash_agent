import asyncio
import os
import subprocess
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ash_agent.persistence.models import Run, RunStatus
from tests.conftest import (
    TEST_DATABASE_URL,
    TEST_QUEUE_NAME,
    TEST_VALKEY_URL,
    post_webhook,
    workflow_run_payload,
)


def run_worker_burst() -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": TEST_DATABASE_URL,
            "VALKEY_URL": TEST_VALKEY_URL,
            "RQ_QUEUE_NAME": TEST_QUEUE_NAME,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "rq.cli",
            "worker",
            "--burst",
            "--url",
            TEST_VALKEY_URL,
            TEST_QUEUE_NAME,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        check=False,
    )


class TestWorker:
    async def test_worker_claims_enqueued_run(self, client, db_engine: AsyncEngine) -> None:
        status_code, body = await post_webhook(client, workflow_run_payload("failure"))
        assert status_code == 202

        result = await asyncio.to_thread(run_worker_burst)
        worker_output = result.stdout + result.stderr
        assert result.returncode == 0, worker_output
        assert "process_run" in worker_output

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with session_factory() as session:
            run = await session.scalar(select(Run))

        assert run is not None
        assert str(run.id) == body["run_id"]
        assert run.status == RunStatus.RUNNING.value
        assert run.attempt == 1
        assert run.started_at is not None

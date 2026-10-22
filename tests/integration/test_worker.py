import asyncio
import os
import subprocess
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ash_agent.agents.graph import build_graph
from ash_agent.config import Settings
from ash_agent.persistence.checkpoints import open_checkpointer
from ash_agent.persistence.models import Run, RunStatus
from ash_agent.queue.jobs import initial_state, process_run
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
            "LLM_BASE_URL": "",
            "LLM_MODEL": "",
            "RETRIEVAL_ENABLED": "false",
        }
    )
    worker_class = "rq.worker.SimpleWorker" if sys.platform == "darwin" else "rq.worker.Worker"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "rq.cli",
            "worker",
            "--burst",
            "--worker-class",
            worker_class,
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
    async def test_worker_runs_graph_to_completion(self, client, db_engine: AsyncEngine) -> None:
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
        assert run.status == RunStatus.PASSED.value
        assert run.attempt == 1
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.last_error is None

    async def test_worker_resumes_from_checkpoint(
        self, client, db_engine: AsyncEngine, test_settings: Settings
    ) -> None:
        status_code, body = await post_webhook(client, workflow_run_payload("failure"))
        assert status_code == 202
        run_id = body["run_id"]
        config = {"configurable": {"thread_id": run_id}}

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with session_factory() as session:
            run = await session.get(Run, UUID(run_id))
            assert run is not None
            run.status = RunStatus.RUNNING.value
            await session.commit()
            state = initial_state(run)

        async with open_checkpointer(test_settings) as saver:
            interrupted_graph = build_graph(
                success_on_attempt=1,
                checkpointer=saver,
                interrupt_after=["reproduce"],
            )
            interrupted = await interrupted_graph.ainvoke(state, config)
        assert interrupted["status"] == "reproducing"

        await asyncio.to_thread(process_run, run_id)

        async with session_factory() as session:
            resumed = await session.get(Run, UUID(run_id))

        assert resumed is not None
        assert resumed.status == RunStatus.PASSED.value
        assert resumed.attempt == 1
        assert resumed.finished_at is not None

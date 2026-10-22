import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from rq import get_current_job
from sqlalchemy.ext.asyncio import AsyncSession

from ash_agent.agents.graph import build_graph
from ash_agent.agents.state import AgentState
from ash_agent.config import get_settings
from ash_agent.integrations import build_llm_client
from ash_agent.persistence.checkpoints import open_checkpointer
from ash_agent.persistence.models import Run, RunStatus
from ash_agent.persistence.postgres import Database
from ash_agent.services.retrieval_service import build_retrieval_service

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {RunStatus.PASSED.value, RunStatus.FAILED.value, RunStatus.ESCALATED.value}
)


def initial_state(run: Run) -> AgentState:
    return {
        "run_id": str(run.id),
        "source": run.source,
        "repository": run.repository,
        "ref": run.ref,
        "commit_sha": run.commit_sha,
        "issue_number": run.issue_number,
        "issue_title": run.issue_title,
        "issue_body": run.issue_body,
        "failure_logs": "",
        "suspected_files": [],
        "repository_path": None,
        "retrieved_chunks": [],
        "retrieval_backend": None,
        "root_cause": None,
        "reproduction_confirmed": False,
        "plan": None,
        "patch_diff": None,
        "test_results": {},
        "attempt": 0,
        "max_attempts": run.max_attempts,
        "status": "pending",
        "last_error": None,
    }


def process_run(run_id: str) -> None:
    asyncio.run(_process_run(run_id))


async def _process_run(run_id: str) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            run = await session.get(Run, UUID(run_id))
            if run is None:
                logger.warning("job references unknown run %s", run_id)
                return
            if run.status in TERMINAL_STATUSES:
                logger.info("run %s already finished with %s, skipping", run_id, run.status)
                return
            if run.status != RunStatus.RUNNING.value:
                run.status = RunStatus.RUNNING.value
                run.started_at = datetime.now(UTC)
                await session.commit()
                logger.info("run %s claimed by worker", run_id)

        async with open_checkpointer(settings) as saver:
            llm_client = build_llm_client(settings)
            retrieval_service = build_retrieval_service(settings)
            graph = build_graph(
                success_on_attempt=settings.mock_repair_success_attempt,
                checkpointer=saver,
                llm_client=llm_client,
                retrieval_service=retrieval_service,
            )
            logger.info(
                "run %s using %s diagnosis and %s retrieval",
                run_id,
                "LLM" if llm_client is not None else "mock",
                "repository" if retrieval_service is not None else "mock",
            )
            config = {
                "configurable": {"thread_id": run_id},
                "recursion_limit": settings.graph_recursion_limit,
            }
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": run_id}})
            if checkpoint is None:
                state = await graph.ainvoke(initial_state(run), config)
            else:
                logger.info("run %s resuming from checkpointer", run_id)
                state = await graph.ainvoke(None, config)

        if state is None:
            raise RuntimeError("agent graph returned no final state")
        await _persist_final_state(database, run_id, state)
        logger.info("run %s finished with status %s", run_id, state.get("status"))
    except Exception as exc:
        job = get_current_job()
        retries_left = job.retries_left if job is not None else 0
        logger.exception("run %s failed (retries_left=%s)", run_id, retries_left)
        await _persist_failure(database, run_id, exc, final=retries_left == 0)
        raise
    finally:
        await database.dispose()


async def _persist_final_state(database: Database, run_id: str, state: AgentState) -> None:
    status = state.get("status", RunStatus.FAILED.value)
    if status not in TERMINAL_STATUSES:
        status = RunStatus.FAILED.value

    async with database.session_factory() as session:
        run = await session.get(Run, UUID(run_id))
        if run is None:
            return
        _apply_state(run, state)
        run.status = status
        if status in TERMINAL_STATUSES:
            run.finished_at = datetime.now(UTC)
        await session.commit()


async def _persist_failure(database: Database, run_id: str, exc: Exception, *, final: bool) -> None:
    async with database.session_factory() as session:
        run = await _get_run(session, run_id)
        if run is None:
            return
        run.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        if final:
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
        await session.commit()


def _apply_state(run: Run, state: AgentState) -> None:
    run.attempt = int(state.get("attempt", 0))
    run.last_error = state.get("last_error")
    if state.get("commit_sha"):
        run.commit_sha = state["commit_sha"]


async def _get_run(session: AsyncSession, run_id: str) -> Run | None:
    return await session.get(Run, UUID(run_id))

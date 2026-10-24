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
from ash_agent.sandbox.executor import build_reproduction_service
from ash_agent.services.memory_service import MemoryService
from ash_agent.services.pull_request_service import build_pull_request_service
from ash_agent.services.repair_service import build_repair_service, cleanup_workspace
from ash_agent.services.retrieval_service import build_retrieval_service

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {RunStatus.PASSED.value, RunStatus.FAILED.value, RunStatus.ESCALATED.value}
)


def initial_state(run: Run, prior_lessons: list[dict] | None = None) -> AgentState:
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
        "reproduction_results": {},
        "plan": None,
        "patch_diff": None,
        "patch_summary": None,
        "patch_edits": [],
        "changed_files": [],
        "patched_files": {},
        "patch_workspace": None,
        "patch_error": None,
        "validation_logs": "",
        "prior_lessons": prior_lessons or [],
        "pr_url": None,
        "pr_number": None,
        "pr_branch": None,
        "pr_error": None,
        "publish_status": None,
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
    memory = MemoryService(database)
    state: AgentState | None = None
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

        prior_lessons = await memory.recall(run.repository)
        async with open_checkpointer(settings) as saver:
            llm_client = build_llm_client(settings)
            retrieval_service = build_retrieval_service(settings)
            reproduction_service = build_reproduction_service(settings)
            repair_service = build_repair_service(settings, llm_client)
            publish_service = build_pull_request_service(settings)
            graph = build_graph(
                success_on_attempt=settings.mock_repair_success_attempt,
                checkpointer=saver,
                llm_client=llm_client,
                retrieval_service=retrieval_service,
                reproduction_service=reproduction_service,
                repair_service=repair_service,
                publish_service=publish_service,
            )
            logger.info(
                "run %s using %s diagnosis, %s retrieval, %s reproduction, "
                "%s repair and %s publishing",
                run_id,
                "LLM" if llm_client is not None else "mock",
                "repository" if retrieval_service is not None else "mock",
                "sandbox" if reproduction_service is not None else "mock",
                "LLM+sandbox" if repair_service is not None else "mock",
                "GitHub" if publish_service is not None else "local",
            )
            config = {
                "configurable": {"thread_id": run_id},
                "recursion_limit": settings.graph_recursion_limit,
            }
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": run_id}})
            if checkpoint is None:
                state = await graph.ainvoke(initial_state(run, prior_lessons), config)
            else:
                logger.info("run %s resuming from checkpointer", run_id)
                state = await graph.ainvoke(None, config)

        if state is None:
            raise RuntimeError("agent graph returned no final state")
        await _persist_final_state(database, run_id, state)
        if state.get("status") == RunStatus.PASSED.value and state.get("patch_diff"):
            failed_tests = (state.get("reproduction_results") or {}).get("failed_tests", [])
            await memory.record(
                run_id=run_id,
                repository=run.repository,
                commit_sha=state.get("commit_sha") or run.commit_sha,
                root_cause=state.get("root_cause"),
                patch_summary=state.get("patch_summary"),
                patch_diff=state.get("patch_diff"),
                failed_tests=[str(test) for test in failed_tests],
            )
        logger.info("run %s finished with status %s", run_id, state.get("status"))
    except Exception as exc:
        job = get_current_job()
        retries_left = job.retries_left if job is not None else 0
        logger.exception("run %s failed (retries_left=%s)", run_id, retries_left)
        await _persist_failure(database, run_id, exc, final=retries_left == 0)
        raise
    finally:
        if state:
            await asyncio.to_thread(cleanup_workspace, state.get("patch_workspace"))
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
    run.patch_summary = state.get("patch_summary")
    run.pr_url = state.get("pr_url")
    run.pr_number = state.get("pr_number")
    run.pr_branch = state.get("pr_branch")
    run.publish_status = state.get("publish_status")
    if state.get("commit_sha"):
        run.commit_sha = state["commit_sha"]


async def _get_run(session: AsyncSession, run_id: str) -> Run | None:
    return await session.get(Run, UUID(run_id))

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from ash_agent.config import get_settings
from ash_agent.persistence.models import Run, RunStatus
from ash_agent.persistence.postgres import Database

logger = logging.getLogger(__name__)

CLAIMABLE_STATUSES = frozenset({RunStatus.PENDING.value, RunStatus.QUEUED.value})


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
            if run.status not in CLAIMABLE_STATUSES:
                logger.info("run %s already in status %s, skipping", run_id, run.status)
                return

            run.status = RunStatus.RUNNING.value
            run.attempt += 1
            run.started_at = datetime.now(UTC)
            await session.commit()
            logger.info("run %s claimed by worker (attempt %d)", run_id, run.attempt)

            # Phase 2 attaches the LangGraph agent here. Until then the worker
            # only proves that delivery, persistence and queue dispatch work.
            logger.info("phase 1 stub: no agent graph configured for run %s", run_id)
    finally:
        await database.dispose()

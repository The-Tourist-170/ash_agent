from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select

from ash_agent.persistence.models import RepairLesson
from ash_agent.persistence.postgres import Database

logger = logging.getLogger(__name__)


class MemoryService:
    """Verified repair lessons scoped by repository.

    The interface mirrors the small part of Mem0 this project needs. Only lessons
    produced by a passed sandbox validation are stored here.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    async def recall(self, repository: str, *, limit: int = 3) -> list[dict]:
        async with self.database.session_factory() as session:
            lessons = (
                await session.scalars(
                    select(RepairLesson)
                    .where(RepairLesson.repository == repository)
                    .order_by(RepairLesson.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "root_cause": lesson.root_cause,
                "patch_summary": lesson.patch_summary,
                "commit_sha": lesson.commit_sha,
            }
            for lesson in lessons
        ]

    async def record(
        self,
        *,
        run_id: str,
        repository: str,
        commit_sha: str | None,
        root_cause: str | None,
        patch_summary: str | None,
        patch_diff: str | None,
        failed_tests: list[str],
    ) -> None:
        if not root_cause or not patch_summary:
            return
        async with self.database.session_factory() as session:
            session.add(
                RepairLesson(
                    run_id=UUID(run_id),
                    repository=repository,
                    commit_sha=commit_sha,
                    root_cause=root_cause,
                    patch_summary=patch_summary,
                    patch_diff=patch_diff,
                    failed_tests=failed_tests,
                )
            )
            await session.commit()
        logger.info("stored verified repair lesson for %s", repository)

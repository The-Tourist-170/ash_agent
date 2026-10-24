from sqlalchemy.ext.asyncio import async_sessionmaker

from ash_agent.persistence.models import Run, WebhookDelivery
from ash_agent.persistence.postgres import Database
from ash_agent.services.memory_service import MemoryService
from tests.conftest import TEST_DATABASE_URL


class TestMemoryService:
    async def test_records_and_recalls_scoped_lessons(self) -> None:
        database = Database(TEST_DATABASE_URL)
        try:
            session_factory = async_sessionmaker(database.engine, expire_on_commit=False)
            async with session_factory() as session:
                session.add(
                    WebhookDelivery(
                        delivery_id="memory-1",
                        event_type="issues",
                        payload={},
                    )
                )
                await session.commit()
                run = Run(
                    delivery_id="memory-1",
                    repository="owner/repo",
                    status="passed",
                )
                session.add(run)
                await session.commit()
                run_id = str(run.id)

            memory = MemoryService(database)
            await memory.record(
                run_id=run_id,
                repository="owner/repo",
                commit_sha="a" * 40,
                root_cause="tax applied before discount",
                patch_summary="apply discount first",
                patch_diff="--- a\n+++ b\n",
                failed_tests=["tests/test_core.py::test_total"],
            )

            lessons = await memory.recall("owner/repo")
            other = await memory.recall("other/repo")

            assert lessons == [
                {
                    "root_cause": "tax applied before discount",
                    "patch_summary": "apply discount first",
                    "commit_sha": "a" * 40,
                }
            ]
            assert other == []
        finally:
            await database.dispose()

    async def test_does_not_store_incomplete_lessons(self) -> None:
        database = Database(TEST_DATABASE_URL)
        try:
            memory = MemoryService(database)
            await memory.record(
                run_id="00000000-0000-0000-0000-000000000000",
                repository="owner/repo",
                commit_sha=None,
                root_cause=None,
                patch_summary=None,
                patch_diff=None,
                failed_tests=[],
            )

            assert await memory.recall("owner/repo") == []
        finally:
            await database.dispose()

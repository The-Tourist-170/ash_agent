from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ash_agent.config import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn) as saver:
        yield saver


async def setup_checkpointer(settings: Settings) -> None:
    async with open_checkpointer(settings) as saver:
        await saver.setup()

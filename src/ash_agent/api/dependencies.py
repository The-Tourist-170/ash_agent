from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from ash_agent.config import Settings
from ash_agent.persistence.postgres import Database


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session_factory() as session:
        yield session


def get_enqueue_queue(request: Request) -> Queue:
    return request.app.state.queue


def get_async_redis(request: Request) -> Redis:
    return request.app.state.async_redis


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_enqueue_queue)]
AsyncRedisDep = Annotated[Redis, Depends(get_async_redis)]

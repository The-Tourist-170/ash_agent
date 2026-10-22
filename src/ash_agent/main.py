import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from ash_agent.api.routes import health, runs, webhooks
from ash_agent.config import Settings, get_settings
from ash_agent.observability.logging import configure_logging
from ash_agent.persistence.postgres import Database
from ash_agent.queue.connection import create_async_redis, create_queue, create_redis_connection

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(settings.database_url)
        redis_connection = create_redis_connection(settings.valkey_url)
        queue = create_queue(settings.rq_queue_name, redis_connection)
        async_redis = create_async_redis(settings.valkey_url)

        app.state.settings = settings
        app.state.database = database
        app.state.redis_connection = redis_connection
        app.state.queue = queue
        app.state.async_redis = async_redis

        if not settings.github_webhook_secret.get_secret_value():
            logger.warning("GITHUB_WEBHOOK_SECRET is empty; all webhook requests will be rejected")
        logger.info("ash-agent API started (environment=%s)", settings.environment)

        try:
            yield
        finally:
            await async_redis.aclose()
            redis_connection.close()
            await database.dispose()

    app = FastAPI(title="ash-agent", version=APP_VERSION, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(runs.router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("ash_agent.main:app", host=settings.api_host, port=settings.api_port)

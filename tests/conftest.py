import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ash_agent.config import Settings, get_settings
from ash_agent.main import create_app
from ash_agent.persistence.checkpoints import setup_checkpointer
from ash_agent.persistence.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ash:ash@localhost:5432/ash_agent_test",
)
TEST_VALKEY_URL = os.getenv("TEST_VALKEY_URL", "redis://localhost:6379/15")
TEST_QUEUE_NAME = "ash_agent:test-jobs"
WEBHOOK_SECRET = "test-secret"


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        valkey_url=TEST_VALKEY_URL,
        rq_queue_name=TEST_QUEUE_NAME,
        github_webhook_secret=WEBHOOK_SECRET,
        log_level="WARNING",
        llm_base_url=None,
        llm_model=None,
    )


@pytest_asyncio.fixture
async def db_engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine) -> AsyncIterator[None]:
    redis = Redis.from_url(TEST_VALKEY_URL)
    redis.flushdb()
    redis.close()
    yield


@pytest_asyncio.fixture(autouse=True)
async def configure_runtime_settings(monkeypatch) -> AsyncIterator[None]:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("VALKEY_URL", TEST_VALKEY_URL)
    monkeypatch.setenv("RQ_QUEUE_NAME", TEST_QUEUE_NAME)
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def checkpoint_schema(test_settings: Settings) -> AsyncIterator[None]:
    await setup_checkpointer(test_settings)
    yield


@pytest_asyncio.fixture
async def app(test_settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(test_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def workflow_run_payload(conclusion: str = "failure") -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": "octo/demo"},
        "workflow_run": {
            "id": 123456789,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "conclusion": conclusion,
            "html_url": "https://github.com/octo/demo/actions/runs/123456789",
        },
    }


def issue_payload(
    action: str = "opened",
    number: int = 6,
    title: str = "[BUG] Calculate tax after applying the discount",
    body: str = "Totals differ by one cent when a discount is applied.",
) -> dict:
    repository = "The-Tourist-170/dummy_project"
    return {
        "action": action,
        "repository": {"full_name": repository, "default_branch": "main"},
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{repository}/issues/{number}",
        },
    }


def webhook_headers(
    body: bytes,
    *,
    event: str = "workflow_run",
    delivery_id: str | None = None,
    secret: str = WEBHOOK_SECRET,
    signature: str | None = None,
) -> dict[str, str]:
    return {
        "X-Hub-Signature-256": signature if signature is not None else sign(body, secret),
        "X-GitHub-Delivery": delivery_id or str(uuid4()),
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


async def post_webhook(client: AsyncClient, payload: dict, **header_kwargs) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    response = await client.post(
        "/webhooks/github",
        content=body,
        headers=webhook_headers(body, **header_kwargs),
    )
    return response.status_code, response.json()

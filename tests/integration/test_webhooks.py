from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ash_agent.persistence.models import DeliveryStatus, Run, RunStatus, WebhookDelivery
from tests.conftest import (
    TEST_QUEUE_NAME,
    TEST_VALKEY_URL,
    post_webhook,
    sign,
    webhook_headers,
    workflow_run_payload,
)


def queue_length() -> int:
    connection = Redis.from_url(TEST_VALKEY_URL)
    try:
        return Queue(TEST_QUEUE_NAME, connection=connection).count
    finally:
        connection.close()


async def counts(db_engine: AsyncEngine) -> tuple[int, int]:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        deliveries = await session.scalar(select(func.count()).select_from(WebhookDelivery))
        runs = await session.scalar(select(func.count()).select_from(Run))
    return deliveries or 0, runs or 0


class TestGithubWebhook:
    async def test_accepts_failed_workflow_and_enqueues_job(
        self, client, db_engine: AsyncEngine
    ) -> None:
        status_code, body = await post_webhook(client, workflow_run_payload("failure"))

        assert status_code == 202
        assert body["status"] == "accepted"
        assert body["run_id"]

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with session_factory() as session:
            delivery = await session.scalar(select(WebhookDelivery))
            run = await session.scalar(select(Run))

        assert delivery is not None
        assert delivery.status == DeliveryStatus.ENQUEUED.value
        assert run is not None
        assert run.status == RunStatus.QUEUED.value
        assert run.commit_sha == "a" * 40
        assert run.rq_job_id == f"run-{run.id}"
        assert queue_length() == 1

    async def test_duplicate_delivery_is_idempotent(self, client, db_engine: AsyncEngine) -> None:
        payload = workflow_run_payload("failure")
        delivery_id = "delivery-123"

        first_status, first_body = await post_webhook(client, payload, delivery_id=delivery_id)
        second_status, second_body = await post_webhook(client, payload, delivery_id=delivery_id)

        assert (first_status, first_body["status"]) == (202, "accepted")
        assert (second_status, second_body["status"]) == (202, "duplicate")
        assert await counts(db_engine) == (1, 1)
        assert queue_length() == 1

    async def test_rejects_invalid_signature(self, client, db_engine: AsyncEngine) -> None:
        status_code, body = await post_webhook(
            client, workflow_run_payload("failure"), signature="sha256=" + "0" * 64
        )

        assert status_code == 401
        assert body["detail"] == "invalid webhook signature"
        assert await counts(db_engine) == (0, 0)
        assert queue_length() == 0

    async def test_ignores_non_failed_workflow(self, client, db_engine: AsyncEngine) -> None:
        status_code, body = await post_webhook(client, workflow_run_payload("success"))

        assert status_code == 202
        assert body["status"] == "ignored"
        assert await counts(db_engine) == (1, 0)
        assert queue_length() == 0

    async def test_requires_delivery_and_event_headers(self, client) -> None:
        body = b"{}"
        headers = {"X-Hub-Signature-256": sign(body), "Content-Type": "application/json"}

        response = await client.post("/webhooks/github", content=body, headers=headers)

        assert response.status_code == 400

    async def test_rejects_invalid_json(self, client) -> None:
        body = b"not-json"
        headers = webhook_headers(body)

        response = await client.post("/webhooks/github", content=body, headers=headers)

        assert response.status_code == 400

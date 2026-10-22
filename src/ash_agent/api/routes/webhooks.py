import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request, status

from ash_agent.api.dependencies import QueueDep, SessionDep, SettingsDep
from ash_agent.api.schemas.webhooks import WebhookAck
from ash_agent.persistence.models import DeliveryStatus, RunStatus
from ash_agent.queue.jobs import process_run
from ash_agent.services.run_service import create_run, store_delivery
from ash_agent.services.webhook_service import extract_failure_context, verify_github_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/github",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAck,
)
async def github_webhook(
    request: Request,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
) -> WebhookAck:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    secret = settings.github_webhook_secret.get_secret_value()
    if not verify_github_signature(secret, raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )

    delivery_id = request.headers.get("X-GitHub-Delivery")
    event_type = request.headers.get("X-GitHub-Event")
    if not delivery_id or not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing X-GitHub-Delivery or X-GitHub-Event header",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload is not valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload must be a JSON object",
        )

    action = payload.get("action")
    repository = (payload.get("repository") or {}).get("full_name")

    delivery, created = await store_delivery(
        session,
        delivery_id=delivery_id,
        event_type=event_type,
        action=action if isinstance(action, str) else None,
        repository=repository if isinstance(repository, str) else None,
        payload=payload,
    )
    if not created:
        await session.commit()
        logger.info("duplicate delivery %s ignored", delivery_id)
        return WebhookAck(
            status="duplicate",
            delivery_id=delivery_id,
            detail="delivery already processed",
        )

    context = extract_failure_context(event_type, payload)
    if context is None:
        delivery.status = DeliveryStatus.IGNORED.value
        await session.commit()
        logger.info("delivery %s (%s) ignored: no repairable failure", delivery_id, event_type)
        return WebhookAck(
            status="ignored",
            delivery_id=delivery_id,
            detail=f"no repairable failure in {event_type} event",
        )

    run = await create_run(session, delivery, context, max_attempts=settings.max_attempts)
    try:
        job = await asyncio.to_thread(
            queue.enqueue,
            process_run,
            str(run.id),
            job_id=f"run-{run.id}",
            job_timeout=settings.run_job_timeout_seconds,
        )
    except Exception as exc:
        message = f"enqueue failed: {type(exc).__name__}"[:1000]
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error = message
        run.status = RunStatus.FAILED.value
        run.last_error = message
        await session.commit()
        logger.exception("failed to enqueue run %s", run.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to enqueue repair job",
        ) from exc

    run.rq_job_id = job.id
    run.status = RunStatus.QUEUED.value
    delivery.status = DeliveryStatus.ENQUEUED.value
    await session.commit()

    logger.info(
        "accepted delivery %s for %s@%s as run %s",
        delivery_id,
        context.repository,
        context.commit_sha[:12],
        run.id,
    )
    return WebhookAck(status="accepted", delivery_id=delivery_id, run_id=run.id)

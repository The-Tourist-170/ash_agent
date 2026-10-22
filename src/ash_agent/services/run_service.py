from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ash_agent.persistence.models import Run, RunStatus, WebhookDelivery
from ash_agent.services.webhook_service import RepairContext


async def store_delivery(
    session: AsyncSession,
    *,
    delivery_id: str,
    event_type: str,
    action: str | None,
    repository: str | None,
    payload: dict,
) -> tuple[WebhookDelivery, bool]:
    delivery = WebhookDelivery(
        delivery_id=delivery_id,
        event_type=event_type,
        action=action,
        repository=repository,
        payload=payload,
    )
    try:
        async with session.begin_nested():
            session.add(delivery)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )
        if existing is None:
            raise
        return existing, False
    return delivery, True


async def create_run(
    session: AsyncSession,
    delivery: WebhookDelivery,
    context: RepairContext,
    *,
    max_attempts: int,
) -> Run:
    run = Run(
        delivery_id=delivery.delivery_id,
        source=context.source,
        repository=context.repository,
        ref=context.ref,
        commit_sha=context.commit_sha,
        workflow_name=context.workflow_name,
        workflow_run_id=context.workflow_run_id,
        run_url=context.run_url,
        issue_number=context.issue_number,
        issue_title=context.issue_title,
        issue_body=context.issue_body,
        status=RunStatus.PENDING.value,
        max_attempts=max_attempts,
    )
    session.add(run)
    await session.flush()
    return run


async def get_run(session: AsyncSession, run_id: UUID) -> Run | None:
    return await session.get(Run, run_id)


async def list_runs(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
) -> list[Run]:
    statement = select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
    if status is not None:
        statement = statement.where(Run.status == status)
    return list((await session.scalars(statement)).all())

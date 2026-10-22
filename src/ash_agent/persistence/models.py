from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DeliveryStatus(StrEnum):
    RECEIVED = "received"
    ENQUEUED = "enqueued"
    IGNORED = "ignored"
    FAILED = "failed"


class RunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ESCALATED = "escalated"


class Base(DeclarativeBase):
    pass


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    repository: Mapped[str | None] = mapped_column(String(255), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DeliveryStatus.RECEIVED.value, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_deliveries.delivery_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    repository: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ref: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    workflow_name: Mapped[str | None] = mapped_column(String(255))
    workflow_run_id: Mapped[int | None] = mapped_column(BigInteger)
    run_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RunStatus.PENDING.value, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    rq_job_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

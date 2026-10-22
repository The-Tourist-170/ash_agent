from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class WebhookAck(BaseModel):
    status: Literal["accepted", "duplicate", "ignored"]
    delivery_id: str
    run_id: UUID | None = None
    detail: str | None = None

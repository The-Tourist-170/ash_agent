from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delivery_id: str
    repository: str
    ref: str | None
    commit_sha: str
    workflow_name: str | None
    workflow_run_id: int | None
    run_url: str | None
    status: str
    attempt: int
    max_attempts: int
    rq_job_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

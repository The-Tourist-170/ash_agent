from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from ash_agent.api.dependencies import SessionDep
from ash_agent.api.schemas.runs import RunRead
from ash_agent.persistence.models import RunStatus
from ash_agent.services.run_service import get_run, list_runs

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunRead])
async def read_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> list[RunRead]:
    runs = await list_runs(
        session,
        limit=limit,
        offset=offset,
        status=run_status.value if run_status else None,
    )
    return [RunRead.model_validate(run) for run in runs]


@router.get("/{run_id}", response_model=RunRead)
async def read_run(run_id: UUID, session: SessionDep) -> RunRead:
    run = await get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return RunRead.model_validate(run)

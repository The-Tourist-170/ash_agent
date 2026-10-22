import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ash_agent.api.dependencies import AsyncRedisDep, DatabaseDep
from ash_agent.api.schemas.health import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(database: DatabaseDep, redis_client: AsyncRedisDep) -> Response:
    components: dict[str, str] = {}
    healthy = True

    try:
        await database.ping()
        components["postgres"] = "ok"
    except Exception as exc:
        logger.warning("postgres readiness check failed: %s", type(exc).__name__)
        components["postgres"] = "error"
        healthy = False

    try:
        await redis_client.ping()
        components["valkey"] = "ok"
    except Exception as exc:
        logger.warning("valkey readiness check failed: %s", type(exc).__name__)
        components["valkey"] = "error"
        healthy = False

    body = HealthResponse(status="ok" if healthy else "degraded", components=components)
    if not healthy:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body

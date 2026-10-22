from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    components: dict[str, str] | None = None

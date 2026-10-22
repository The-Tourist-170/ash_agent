from ash_agent.config import Settings

from .llm import OpenAICompatibleClient


def build_llm_client(settings: Settings) -> OpenAICompatibleClient | None:
    if not settings.llm_configured:
        return None
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url or "",
        model=settings.llm_model or "",
        api_key=settings.llm_api_key.get_secret_value(),
        timeout_seconds=settings.llm_timeout_seconds,
    )

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


class LLMError(RuntimeError):
    pass


class Diagnosis(BaseModel):
    root_cause: str = Field(description="Most likely root cause of the failure.")
    suspected_files: list[str] = Field(
        default_factory=list,
        description="Repository-relative files that should be inspected.",
    )
    plan: str = Field(description="Concrete repair plan.")


class DiagnosisLLM(Protocol):
    async def diagnose(self, system_prompt: str, user_prompt: str) -> Diagnosis: ...


class _StripAuthorization(httpx.Auth):
    """Remove the SDK's bearer header for endpoints that reject any auth."""

    def auth_flow(self, request: httpx.Request):
        request.headers.pop("Authorization", None)
        yield request


@dataclass(frozen=True)
class OpenAICompatibleClient:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 60.0

    async def diagnose(self, system_prompt: str, user_prompt: str) -> Diagnosis:
        auth = None if self.api_key else _StripAuthorization()
        http_client = httpx.AsyncClient(auth=auth, timeout=self.timeout_seconds)
        try:
            model = ChatOpenAI(
                model=self.model,
                base_url=self.base_url_with_v1,
                api_key=self.api_key or "unused",
                http_async_client=http_client,
                temperature=0,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            structured = model.with_structured_output(Diagnosis, method="function_calling")
            result = await structured.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
        except Exception as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        finally:
            await http_client.aclose()

        if isinstance(result, dict):
            result = Diagnosis.model_validate(result)
        if not isinstance(result, Diagnosis):
            raise LLMError("LLM returned no structured diagnosis")
        return result

    @property
    def base_url_with_v1(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

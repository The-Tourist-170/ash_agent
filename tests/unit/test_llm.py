import httpx

from ash_agent.agents.nodes.diagnose import make_llm_diagnose_node
from ash_agent.integrations.llm import (
    Diagnosis,
    LLMError,
    OpenAICompatibleClient,
    _StripAuthorization,
)


def base_state() -> dict:
    return {
        "run_id": "llm-test",
        "source": "issue",
        "repository": "The-Tourist-170/dummy_project",
        "ref": "main",
        "commit_sha": None,
        "issue_number": 3,
        "issue_title": "[BUG] Phone normalization leaves spaces behind",
        "issue_body": "normalize_phone returns spaces.",
        "attempt": 0,
        "max_attempts": 3,
    }


class FakeLLM:
    def __init__(self, result: Diagnosis | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def diagnose(self, system_prompt: str, user_prompt: str) -> Diagnosis:
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return Diagnosis(root_cause="default", plan="default")


class TestBaseUrl:
    def test_appends_v1_when_missing(self) -> None:
        client = OpenAICompatibleClient(base_url="http://localhost:55990", model="m")
        assert client.base_url_with_v1 == "http://localhost:55990/v1"

    def test_keeps_existing_v1(self) -> None:
        client = OpenAICompatibleClient(base_url="http://localhost:55990/v1", model="m")
        assert client.base_url_with_v1 == "http://localhost:55990/v1"


class TestStripAuthorization:
    def test_removes_bearer_header(self) -> None:
        request = httpx.Request(
            "POST", "http://localhost/v1/chat/completions", headers={"Authorization": "Bearer x"}
        )

        next(_StripAuthorization().auth_flow(request))

        assert "Authorization" not in request.headers


class TestLLMDiagnoseNode:
    async def test_uses_llm_diagnosis(self) -> None:
        llm = FakeLLM(
            Diagnosis(
                root_cause="phone normalization keeps spaces",
                suspected_files=["src/dummy_project/core.py"],
                plan="strip non-digits in normalize_phone",
            )
        )
        node = make_llm_diagnose_node(llm)

        result = await node(base_state())

        assert result["root_cause"] == "phone normalization keeps spaces"
        assert result["suspected_files"] == ["src/dummy_project/core.py"]
        assert result["plan"] == "strip non-digits in normalize_phone"
        assert result["status"] == "diagnosing"

    async def test_falls_back_to_mock_on_error(self) -> None:
        node = make_llm_diagnose_node(FakeLLM(error=LLMError("boom")))

        result = await node(base_state())

        assert result["root_cause"].startswith("mock diagnosis: issue #3")

    async def test_falls_back_when_root_cause_is_empty(self) -> None:
        node = make_llm_diagnose_node(
            FakeLLM(Diagnosis(root_cause="", suspected_files=[], plan=""))
        )

        result = await node(base_state())

        assert result["root_cause"].startswith("mock diagnosis")


class TestDiagnosisSchema:
    def test_suspected_files_defaults_to_empty(self) -> None:
        diagnosis = Diagnosis(root_cause="x", plan="y")

        assert diagnosis.suspected_files == []
        assert "suspected_files" in Diagnosis.model_json_schema()["properties"]

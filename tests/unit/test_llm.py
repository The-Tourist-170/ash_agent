import pytest

from ash_agent.agents.nodes.diagnose import make_llm_diagnose_node
from ash_agent.integrations.llm import LLMError, OpenAICompatibleClient, parse_json_object


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
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {}
        self.error = error

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if self.error is not None:
            raise self.error
        return self.result


class TestParseJsonObject:
    def test_parses_plain_json(self) -> None:
        assert parse_json_object('{"root_cause": "x"}') == {"root_cause": "x"}

    def test_strips_markdown_fence(self) -> None:
        assert parse_json_object('```json\n{"root_cause": "x"}\n```') == {"root_cause": "x"}

    def test_extracts_object_from_surrounding_text(self) -> None:
        content = 'Here is the result:\n{"root_cause": "x"}\nThanks'
        assert parse_json_object(content) == {"root_cause": "x"}

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(LLMError, match="not valid JSON"):
            parse_json_object("no json here")

    def test_rejects_non_object(self) -> None:
        with pytest.raises(LLMError, match="must be a JSON object"):
            parse_json_object('["root_cause"]')


class TestEndpoint:
    def test_appends_v1_when_missing(self) -> None:
        client = OpenAICompatibleClient(base_url="http://localhost:55990", model="m")
        assert client.endpoint == "http://localhost:55990/v1/chat/completions"

    def test_keeps_existing_v1(self) -> None:
        client = OpenAICompatibleClient(base_url="http://localhost:55990/v1", model="m")
        assert client.endpoint == "http://localhost:55990/v1/chat/completions"


class TestLLMDiagnoseNode:
    async def test_uses_llm_diagnosis(self) -> None:
        llm = FakeLLM(
            {
                "root_cause": "phone normalization keeps spaces",
                "suspected_files": ["src/dummy_project/core.py"],
                "plan": "strip non-digits in normalize_phone",
            }
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
        node = make_llm_diagnose_node(FakeLLM({"root_cause": "", "suspected_files": []}))

        result = await node(base_state())

        assert result["root_cause"].startswith("mock diagnosis")

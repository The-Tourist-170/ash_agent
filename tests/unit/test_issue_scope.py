import pytest

from ash_agent.intelligence.issue_scope import (
    IssueScopeService,
    _runtime_commands,
    build_scope_service,
)


class FakeLLM:
    def __init__(
        self,
        target_tests: list[str] | None = None,
        error: Exception | None = None,
        scope_files: list[str] | None = None,
        scope_symbols: list[str] | None = None,
    ):
        self.target_tests = target_tests or []
        self.error = error
        self.scope_files = scope_files or []
        self.scope_symbols = scope_symbols or []

    async def complete_structured(self, schema, system_prompt, user_prompt):
        if self.error:
            raise self.error
        return schema(
            target_tests=self.target_tests,
            scope_files=self.scope_files,
            scope_symbols=self.scope_symbols,
            reasoning="test scope",
        )


def state(
    source: str = "issue",
    failed: list[str] | None = None,
    title: str = "[BUG] Addition subtracts instead of adding",
) -> dict:
    return {
        "source": source,
        "issue_title": title,
        "issue_body": "add(2, 3) returns -1",
        "root_cause": "add uses subtraction",
        "reproduction_results": {
            "failed_tests": failed
            or [
                "tests/test_calculator.py::test_addition_returns_sum",
                "tests/test_calculator.py::test_subtraction_returns_difference",
                "tests/test_calculator.py::test_multiplication_returns_product",
                "tests/test_calculator.py::test_division_preserves_fractional_result",
            ]
        },
        "retrieved_chunks": [],
    }


class TestIssueScopeService:
    async def test_llm_selects_only_issue_related_test(self) -> None:
        llm = FakeLLM(["tests/test_calculator.py::test_addition_returns_sum"])
        outcome = await IssueScopeService(llm).determine(state())

        assert outcome.target_tests == ["tests/test_calculator.py::test_addition_returns_sum"]
        assert "tests/test_calculator.py::test_subtraction_returns_difference" in (
            outcome.unrelated_tests
        )
        assert outcome.reasoning == "test scope"

    async def test_lexical_fallback_selects_addition(self) -> None:
        llm = FakeLLM(error=RuntimeError("llm down"))
        outcome = await IssueScopeService(llm).determine(state())

        assert outcome.target_tests == ["tests/test_calculator.py::test_addition_returns_sum"]

    async def test_workflow_run_scopes_all_failures(self) -> None:
        outcome = await IssueScopeService(FakeLLM()).determine(
            state(source="workflow_run", title="CI failure")
        )

        assert len(outcome.target_tests) == 4
        assert outcome.unrelated_tests == []

    async def test_unmappable_issue_returns_no_target(self) -> None:
        failed = [
            "tests/test_calculator.py::test_division_by_zero_is_reported",
            "tests/test_calculator.py::test_binary_operation_requires_two_operands",
        ]
        outcome = await IssueScopeService(None).determine(
            state(title="[BUG] something entirely unrelated", failed=failed)
        )

        assert outcome.target_tests == []
        assert outcome.unrelated_tests == failed

    async def test_build_scope_service_requires_llm(self) -> None:
        assert build_scope_service(None) is None

    async def test_rejects_llm_tests_unrelated_to_explicit_script(self, tmp_path) -> None:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "src" / "calculator_bug_lab").mkdir(parents=True)
        (repo / "scripts" / "compose_smoke.py").write_text("def main():\n    print(message)\n")
        (repo / "docker-compose.yml").write_text(
            "services:\n"
            "  calculator-smoke:\n"
            "    image: python:3.12-slim\n"
            '    command: ["python", "scripts/compose_smoke.py"]\n'
        )
        (repo / "src" / "calculator_bug_lab" / "__init__.py").write_text(
            "def add(a, b):\n    return a - b\n"
            "def multiply(a, b):\n    return a + b\n"
            "def divide(a, b):\n    return a // b\n"
        )
        compose_state = {
            "source": "issue",
            "issue_title": "[BUG] Docker Compose smoke service crashes with NameError",
            "issue_body": (
                "The Compose smoke service exits with a NameError because "
                "`scripts/compose_smoke.py` prints `message`, which is not defined.\n\n"
                "Reproduction: run `docker compose run --rm calculator-smoke`."
            ),
            "repository_path": str(repo),
            "suspected_files": [
                "scripts/compose_smoke.py",
                "src/calculator_bug_lab/__init__.py",
            ],
            "reproduction_results": {
                "failed_tests": [
                    "tests/test_calculator.py::test_multiplication_returns_product",
                    "tests/test_calculator.py::test_division_preserves_fractional_result",
                ]
            },
            "retrieved_chunks": [
                {
                    "path": "tests/test_calculator.py",
                    "content": (
                        "def test_multiplication_returns_product():\n"
                        "    assert multiply(4, 3) == 12\n\n"
                        "def test_division_preserves_fractional_result():\n"
                        "    assert divide(7, 2) == 3.5\n"
                    ),
                }
            ],
        }
        llm = FakeLLM(
            target_tests=[
                "tests/test_calculator.py::test_multiplication_returns_product",
                "tests/test_calculator.py::test_division_preserves_fractional_result",
            ],
            scope_files=["src/calculator_bug_lab/__init__.py"],
            scope_symbols=["multiply", "divide"],
        )

        outcome = await IssueScopeService(llm).determine(compose_state)

        assert outcome.target_tests == []
        assert outcome.validation_mode == "command"
        assert outcome.runtime_commands == [["python", "scripts/compose_smoke.py"]]
        assert outcome.runtime_expected_output is None
        assert outcome.scope_files == [
            "scripts/compose_smoke.py",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ]
        assert "src/calculator_bug_lab/__init__.py" not in outcome.scope_files

    async def test_runtime_plan_extracts_expected_output(self, tmp_path) -> None:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "tool.py").write_text("print('wrong')\n")
        runtime_state = {
            "source": "issue",
            "issue_title": "[BUG] Tool prints the wrong result",
            "issue_body": ("Reproduction: run `python scripts/tool.py`.\n\nExpected output: `42`"),
            "repository_path": str(repo),
            "suspected_files": ["scripts/tool.py"],
            "reproduction_results": {"failed_tests": []},
            "retrieved_chunks": [],
        }

        outcome = await IssueScopeService(None).determine(runtime_state)

        assert outcome.validation_mode == "command"
        assert outcome.runtime_commands == [["python", "scripts/tool.py"]]
        assert outcome.runtime_expected_output == "42"
        assert outcome.scope_files == ["scripts/tool.py"]

    @pytest.mark.parametrize(
        ("symbol", "arguments", "expected"),
        [("underroot", "9", "3.0"), ("raise_to_power", "2, 3", "8")],
    )
    async def test_environment_prefixed_runtime_issue(
        self, tmp_path, symbol, arguments, expected
    ) -> None:
        root = tmp_path / "repo"
        package = root / "src" / "calculator_bug_lab"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            "def underroot(value):\n    return value\n\n"
            "def raise_to_power(left, right):\n    return left + right\n"
        )
        code = f"from calculator_bug_lab import {symbol}; print({symbol}({arguments}))"
        runtime_state = {
            **state(title=f"[BUG] {symbol} runtime behavior"),
            "issue_body": (
                f"## Reproduction\n```bash\nPYTHONPATH=src python3 -c '{code}'\n```\n"
                f"## Expected behavior\n`{symbol}({arguments})` should return `{expected}`.\n"
                "The CLI can run once the separate console-entry-point issue is fixed."
            ),
            "repository_path": str(root),
            "suspected_files": ["src/calculator_bug_lab/__init__.py"],
        }

        outcome = await IssueScopeService(None).determine(runtime_state)

        assert outcome.validation_mode == "command"
        assert outcome.runtime_commands == [["env", "PYTHONPATH=src", "python3", "-c", code]]
        assert outcome.runtime_expected_output == expected
        assert outcome.scope_files == ["src/calculator_bug_lab/__init__.py"]
        assert outcome.scope_symbols == [symbol]
        assert outcome.target_tests == []
        assert outcome.unrelated_tests == runtime_state["reproduction_results"]["failed_tests"]


class TestRuntimeCommandParsing:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            (
                'PYTHONPATH=src LABEL="two words" python3 -c \'print("$LABEL")\'',
                ["env", "PYTHONPATH=src", "LABEL=two words", "python3", "-c", 'print("$LABEL")'],
            ),
            ("PYTHONPATH=src", None),
            ("INVALID-NAME=value python3 script.py", None),
            ("PYTHONPATH=src unsupported script.py", None),
            ("PYTHONPATH=src python3 -c 'unterminated", None),
            ("PYTHONPATH=src uv run pytest tests/test_calculator.py", None),
            ("python3 script.py", ["python3", "script.py"]),
        ],
    )
    def test_assignment_parsing(self, command, expected) -> None:
        parsed = _runtime_commands({}, f"```bash\n{command}\n```")

        assert parsed == ([expected] if expected else [])

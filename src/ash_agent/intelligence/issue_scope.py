from __future__ import annotations

import ast
import json
import logging
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from ash_agent.integrations.llm import StructuredLLM
from ash_agent.intelligence.chunking import tokenize

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You scope a GitHub issue to the minimum set of source/config files, symbols and tests "
    "needed to prove and repair it. A failing test is in scope only if its assertions directly "
    "verify the reported behavior. Never include tests just because they are failing. If the "
    "issue is about a script or runtime command with no pytest coverage, return an empty "
    "target_tests list and list the files/symbols that must change."
)

_GENERIC_TOKENS = frozenset(
    {
        "bug",
        "bugs",
        "error",
        "errors",
        "expected",
        "actual",
        "fail",
        "failed",
        "failing",
        "failure",
        "instead",
        "issue",
        "result",
        "results",
        "return",
        "returns",
        "should",
        "test",
        "tests",
        "when",
        "with",
    }
)

_PATH_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|toml|ya?ml|json|cfg|ini|txt|md))")
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_]\w*\b")


class IssueScope(BaseModel):
    target_tests: list[str] = Field(
        default_factory=list,
        description="Subset of failing test IDs that directly prove the issue.",
    )
    scope_files: list[str] = Field(
        default_factory=list,
        description="Repository-relative source/config files that may be edited.",
    )
    scope_symbols: list[str] = Field(
        default_factory=list,
        description="Functions/classes that may be edited; empty means file-level scope.",
    )
    reasoning: str = Field(description="Why these tests/files belong to the issue.")


@dataclass(frozen=True)
class ScopeOutcome:
    target_tests: list[str]
    unrelated_tests: list[str]
    reasoning: str
    scope_files: list[str] = field(default_factory=list)
    scope_symbols: list[str] = field(default_factory=list)
    validation_mode: str = "pytest"
    runtime_commands: list[list[str]] = field(default_factory=list)
    runtime_expected_exit: int = 0
    runtime_expected_output: str | None = None
    runtime_description: str = ""


@dataclass(frozen=True)
class RuntimePlan:
    commands: list[list[str]]
    scope_files: list[str]
    scope_symbols: list[str]
    expected_output: str | None
    description: str


class IssueScopeService:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self.llm = llm

    async def determine(self, state: dict) -> ScopeOutcome:
        failed_tests = _failed_tests(state)
        explicit_files = _explicit_files(state)
        runtime = _runtime_plan(state) if state.get("source") == "issue" else None
        if not failed_tests:
            if runtime is not None:
                return _runtime_outcome(runtime, [], "no pytest failures; runtime plan")
            return ScopeOutcome(
                [],
                [],
                "no failing tests to scope",
                explicit_files,
                _issue_symbols(state, explicit_files),
            )

        if state.get("source") != "issue":
            files = _fallback_files(state)
            return ScopeOutcome(
                failed_tests,
                [],
                "workflow run: all failing tests are in scope",
                files,
                _symbols_from_tests(state, failed_tests, files),
            )

        scope_files = explicit_files or _fallback_files(state)
        scope_symbols: list[str] = []
        if self.llm is not None:
            try:
                result = await self.llm.complete_structured(
                    IssueScope,
                    _SYSTEM_PROMPT,
                    _scope_prompt(state, failed_tests, explicit_files),
                )
                if not isinstance(result, IssueScope):
                    result = IssueScope.model_validate(result)

                scope_files = explicit_files or (_clean_paths(result.scope_files) or scope_files)
                scope_symbols = _clean_symbols(result.scope_symbols)

                claimed = [test for test in result.target_tests if test in failed_tests]
                target = [
                    test
                    for test in claimed
                    if _test_is_anchored(
                        state,
                        test,
                        scope_files,
                        scope_symbols,
                        explicit_files=explicit_files,
                    )
                ]
                if claimed and len(target) != len(claimed):
                    dropped = [test for test in claimed if test not in target]
                    logger.warning(
                        "dropped scope tests without issue evidence: %s",
                        dropped,
                    )
                if target:
                    derived = set(_symbols_from_tests(state, target, scope_files))
                    derived.update(_issue_symbols(state, scope_files))
                    if derived:
                        scope_symbols = [
                            symbol for symbol in scope_symbols if _bare_symbol(symbol) in derived
                        ] or sorted(derived)
                    if not scope_symbols:
                        scope_symbols = _symbols_from_tests(state, target, scope_files)
                    unrelated = [test for test in failed_tests if test not in target]
                    return ScopeOutcome(
                        target,
                        unrelated,
                        result.reasoning,
                        scope_files,
                        scope_symbols,
                    )
            except Exception as exc:
                logger.warning("LLM issue scoping failed: %s", type(exc).__name__)

        target = _lexical_targets(state, failed_tests)
        if target:
            if not scope_symbols:
                scope_symbols = _symbols_from_tests(state, target, scope_files)
            unrelated = [test for test in failed_tests if test not in target]
            return ScopeOutcome(
                target,
                unrelated,
                "lexical issue-to-test match",
                scope_files,
                scope_symbols,
            )

        if runtime is not None:
            return _runtime_outcome(
                runtime,
                failed_tests,
                "no issue-related pytest failure; runtime plan",
            )

        return ScopeOutcome(
            [],
            failed_tests,
            "could not map the issue to a failing test",
            scope_files,
            scope_symbols or _issue_symbols(state, scope_files),
        )


def _runtime_outcome(
    plan: RuntimePlan,
    unrelated_tests: list[str],
    reason: str,
) -> ScopeOutcome:
    return ScopeOutcome(
        [],
        unrelated_tests,
        reason,
        plan.scope_files,
        plan.scope_symbols,
        "command",
        plan.commands,
        0,
        plan.expected_output,
        plan.description,
    )


def _runtime_plan(state: dict) -> RuntimePlan | None:
    text = _issue_text(state)
    if _packaging_issue(text):
        return None
    commands = _runtime_commands(state, text)
    if not commands:
        return None

    files = _explicit_files(state)
    for command in commands:
        script = _command_script(command)
        if script and script not in files:
            files.append(script)
    files = _clean_paths(files) or _fallback_files(state)
    if not files:
        return None

    return RuntimePlan(
        commands=commands,
        scope_files=files,
        scope_symbols=_issue_symbols(state, files),
        expected_output=_expected_output(text),
        description="runtime command: " + " && ".join(" ".join(c) for c in commands),
    )


def _packaging_issue(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "pyproject.toml",
            "console script",
            "console entry point",
            "entry point",
            "wheel",
            "packaging",
            "build failure",
            "install fail",
            "not callable",
        )
    )


def _runtime_commands(state: dict, text: str) -> list[list[str]]:
    for snippet in _command_snippets(text):
        for raw in snippet.splitlines():
            line = raw.strip()
            if not line:
                continue
            if "pytest" in line:
                continue
            if line.startswith(("docker compose", "docker-compose")):
                service = _compose_service(line)
                command = _compose_command(state, service)
                if command:
                    return [command]
                continue
            if line.startswith(
                ("python ", "python3 ", "pytest ", "uv run ", "node ", "npm ", "bash ", "sh ")
            ):
                try:
                    return [shlex.split(line)]
                except ValueError:
                    continue
    return []


def _command_snippets(text: str) -> list[str]:
    fenced = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    inline = re.findall(r"`([^`\n]+)`", text)
    return [*fenced, *inline]


def _compose_service(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for index, token in enumerate(tokens):
        if token != "run":
            continue
        for candidate in tokens[index + 1 :]:
            if not candidate.startswith("-"):
                return candidate
    return None


def _compose_command(state: dict, service: str | None) -> list[str] | None:
    root = state.get("repository_path")
    if not root:
        return None
    for name in _COMPOSE_FILES:
        path = Path(str(root)) / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        current = None
        for line in lines:
            service_match = re.match(r"^\s{2}([\w.-]+):\s*$", line)
            if service_match:
                current = service_match.group(1)
                continue
            if service and current != service:
                continue
            command_match = re.match(r"^\s+command:\s*(.+?)\s*$", line)
            if command_match:
                return _parse_command_value(command_match.group(1))
    return None


def _parse_command_value(value: str) -> list[str] | None:
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list) and parsed:
            return [str(part) for part in parsed]
        return None
    try:
        return shlex.split(value)
    except ValueError:
        return None


def _command_script(command: list[str]) -> str | None:
    for token in command:
        if token.endswith((".py", ".sh")) and not token.startswith("-"):
            return token
    return None


def _expected_output(text: str) -> str | None:
    patterns = (
        r"expected\s+(?:output|stdout|result)s?\s*:\s*`([^`]+)`",
        r"expected\s+(?:output|stdout|result)s?\s*:\s*\"([^\"]+)\"",
        r"should\s+(?:print|output)\s+`([^`]+)`",
        r"should\s+(?:print|output)\s+\"([^\"]+)\"",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        if not value or value.startswith(("python", "uv ", "docker", "pytest")):
            continue
        if "/" in value or value.endswith((".py", ".yml", ".yaml", ".toml")):
            continue
        return value
    return None


def _failed_tests(state: dict) -> list[str]:
    reproduction = state.get("reproduction_results") or {}
    tests = reproduction.get("failed_tests") or []
    return [str(test) for test in tests if str(test).strip()]


def _issue_text(state: dict) -> str:
    return " ".join(str(state.get(key) or "") for key in ("issue_title", "issue_body"))


def _issue_tokens(state: dict) -> set[str]:
    return set(tokenize(_issue_text(state))) - _GENERIC_TOKENS


def _lexical_targets(state: dict, failed_tests: list[str]) -> list[str]:
    issue_tokens = _issue_tokens(state)
    if not issue_tokens:
        return failed_tests if len(failed_tests) == 1 else []

    scored: list[tuple[int, str]] = []
    for test in failed_tests:
        test_name = test.split("::")[-1]
        test_tokens = set(tokenize(test_name)) - _GENERIC_TOKENS
        scored.append((len(issue_tokens & test_tokens), test))

    best = max((score for score, _ in scored), default=0)
    if best == 0:
        return failed_tests if len(failed_tests) == 1 else []
    return [test for score, test in scored if score == best]


def _test_is_anchored(
    state: dict,
    test: str,
    scope_files: list[str],
    scope_symbols: list[str],
    *,
    explicit_files: list[str],
) -> bool:
    test_name = test.split("::")[-1]
    test_tokens = set(tokenize(test_name)) - _GENERIC_TOKENS
    if _issue_tokens(state) & test_tokens:
        return True

    test_code = _test_code(state, test_name)
    if explicit_files:
        issue_tokens = _issue_tokens(state)
        for symbol in scope_symbols:
            bare = _bare_symbol(symbol)
            if bare in issue_tokens and _identifier_in_code(bare, test_code):
                return True
        return False

    for symbol in scope_symbols:
        if _identifier_in_code(_bare_symbol(symbol), test_code):
            return True
    for path in scope_files:
        stem = _module_stem(path)
        if stem and _identifier_in_code(stem, test_code):
            return True
    return False


def _test_code(state: dict, test_name: str) -> str:
    chunks = state.get("retrieved_chunks") or []
    specific: list[str] = []
    all_tests: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        path = str(chunk.get("path") or "")
        if not path.startswith("tests/"):
            continue
        content = str(chunk.get("content") or "")
        all_tests.append(content)
        if re.search(rf"\bdef\s+{re.escape(test_name)}\s*\(", content):
            specific.append(content)
    return "\n".join(specific or all_tests)


def _specific_test_code(state: dict, test_name: str) -> str:
    chunks = state.get("retrieved_chunks") or []
    specific: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if not str(chunk.get("path") or "").startswith("tests/"):
            continue
        content = str(chunk.get("content") or "")
        if re.search(rf"\bdef\s+{re.escape(test_name)}\s*\(", content):
            specific.append(content)
    return "\n".join(specific)


def _explicit_files(state: dict) -> list[str]:
    text = _issue_text(state)
    files = _clean_paths(_PATH_RE.findall(text))
    if "compose" in text.lower():
        for candidate in _COMPOSE_FILES:
            if candidate not in files:
                files.append(candidate)
    return files


def _fallback_files(state: dict) -> list[str]:
    suspected = _clean_paths(state.get("suspected_files") or [])
    suspected = [path for path in suspected if not path.startswith("tests/")]
    if suspected:
        return suspected

    chunks = state.get("retrieved_chunks") or []
    files = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        path = str(chunk.get("path") or "")
        if path and not path.startswith("tests/") and path not in files:
            files.append(path)
        if len(files) >= 5:
            break
    return files


def _clean_paths(paths: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in paths:
        path = str(value).strip().replace("\\", "/")
        if not path or path.startswith("/"):
            continue
        if path.startswith("./"):
            path = path[2:]
        candidate = PurePosixPath(path)
        if ".." in candidate.parts:
            continue
        normalized = str(candidate)
        if normalized.startswith("tests/"):
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _clean_symbols(symbols: list[str]) -> list[str]:
    cleaned = []
    for symbol in symbols:
        value = str(symbol).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _symbols_from_tests(state: dict, target_tests: list[str], scope_files: list[str]) -> list[str]:
    identifiers: set[str] = set()
    for test in target_tests:
        identifiers.update(_IDENTIFIER_RE.findall(_specific_test_code(state, test.split("::")[-1])))
    if not identifiers:
        return []

    symbols: list[str] = []
    root = state.get("repository_path")
    for path in scope_files:
        if not path.endswith(".py"):
            continue
        source = ""
        if root:
            file_path = Path(str(root)) / path
            try:
                source = file_path.read_text(encoding="utf-8")
            except OSError:
                source = ""
        if not source:
            for chunk in state.get("retrieved_chunks") or []:
                if isinstance(chunk, dict) and str(chunk.get("path")) == path:
                    source += str(chunk.get("content") or "")
        if not source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in identifiers
                and node.name not in symbols
            ):
                symbols.append(node.name)
    return symbols


def _issue_symbols(state: dict, scope_files: list[str]) -> list[str]:
    tokens = _issue_tokens(state)
    symbols: list[str] = []
    root = state.get("repository_path")
    for path in scope_files:
        if not path.endswith(".py"):
            continue
        source = ""
        if root:
            try:
                source = (Path(str(root)) / path).read_text(encoding="utf-8")
            except OSError:
                source = ""
        if not source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in tokens
                and node.name not in symbols
            ):
                symbols.append(node.name)
    return symbols


def _bare_symbol(symbol: str) -> str:
    return symbol.split(":")[-1].split(".")[-1]


def _identifier_in_code(identifier: str, code: str) -> bool:
    return bool(identifier) and bool(re.search(rf"\b{re.escape(identifier)}\b", code))


def _module_stem(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.stem == "__init__":
        return pure.parent.name
    return pure.stem


def _scope_prompt(state: dict, failed_tests: list[str], explicit_files: list[str]) -> str:
    sections = [
        f"Issue title: {state.get('issue_title') or ''}",
        f"Issue body:\n{str(state.get('issue_body') or '')[:4000]}",
        f"Root cause:\n{state.get('root_cause') or ''}",
        "Failing tests (not automatically in scope):\n"
        + "\n".join(f"- {test}" for test in failed_tests),
    ]
    if explicit_files:
        sections.append(
            "Files explicitly named by the issue (hard scope):\n"
            + "\n".join(f"- {path}" for path in explicit_files)
        )
    suspected = _clean_paths(state.get("suspected_files") or [])
    if suspected:
        sections.append("Suspected files:\n" + "\n".join(f"- {path}" for path in suspected))

    chunks = state.get("retrieved_chunks") or []
    test_chunks = [
        chunk
        for chunk in chunks
        if isinstance(chunk, dict) and str(chunk.get("path") or "").startswith("tests/")
    ]
    if test_chunks:
        snippets = []
        for chunk in test_chunks[:4]:
            snippets.append(
                f"--- {chunk.get('path')}:{chunk.get('start_line')}-"
                f"{chunk.get('end_line')} ---\n{str(chunk.get('content') or '')[:1200]}"
            )
        sections.append("Test code:\n" + "\n".join(snippets))

    sections.append(
        "Return: target_tests (only tests whose assertions directly prove the issue; empty if "
        "there is no such pytest test), scope_files (source/config files that must change, never "
        "tests), and scope_symbols (functions/classes that may change). A failing test is not in "
        "scope merely because it fails. For a script/runtime issue, prefer the named script and "
        "its function; do not pull in unrelated arithmetic tests."
    )
    return "\n\n".join(sections)[:16_000]


def build_scope_service(llm: StructuredLLM | None) -> IssueScopeService | None:
    return IssueScopeService(llm) if llm is not None else None

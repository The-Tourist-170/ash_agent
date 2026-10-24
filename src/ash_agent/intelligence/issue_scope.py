from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ash_agent.integrations.llm import StructuredLLM
from ash_agent.intelligence.chunking import tokenize

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You scope a GitHub issue to the minimum set of failing tests that directly prove it. "
    "Return only failing test IDs from the provided list. Never include tests that cover "
    "unrelated pre-existing bugs."
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


class IssueScope(BaseModel):
    target_tests: list[str] = Field(
        description="Subset of failing test IDs directly related to the issue."
    )
    reasoning: str = Field(description="Why these tests belong to the issue.")


@dataclass(frozen=True)
class ScopeOutcome:
    target_tests: list[str]
    unrelated_tests: list[str]
    reasoning: str


class IssueScopeService:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self.llm = llm

    async def determine(self, state: dict) -> ScopeOutcome:
        failed_tests = _failed_tests(state)
        if not failed_tests:
            return ScopeOutcome([], [], "no failing tests to scope")

        if state.get("source") != "issue":
            return ScopeOutcome(
                failed_tests,
                [],
                "workflow run: all failing tests are in scope",
            )

        if self.llm is not None:
            try:
                result = await self.llm.complete_structured(
                    IssueScope,
                    _SYSTEM_PROMPT,
                    _scope_prompt(state, failed_tests),
                )
                if not isinstance(result, IssueScope):
                    result = IssueScope.model_validate(result)
                target = [test for test in result.target_tests if test in failed_tests]
                if target:
                    unrelated = [test for test in failed_tests if test not in target]
                    return ScopeOutcome(target, unrelated, result.reasoning)
            except Exception as exc:
                logger.warning("LLM issue scoping failed: %s", type(exc).__name__)

        target = _lexical_targets(state, failed_tests)
        if target:
            unrelated = [test for test in failed_tests if test not in target]
            return ScopeOutcome(target, unrelated, "lexical issue-to-test match")

        return ScopeOutcome(
            [],
            failed_tests,
            "could not map the issue to any failing test",
        )


def _failed_tests(state: dict) -> list[str]:
    reproduction = state.get("reproduction_results") or {}
    tests = reproduction.get("failed_tests") or []
    return [str(test) for test in tests if str(test).strip()]


def _lexical_targets(state: dict, failed_tests: list[str]) -> list[str]:
    issue_text = " ".join(str(state.get(key) or "") for key in ("issue_title", "issue_body"))
    issue_tokens = set(tokenize(issue_text)) - _GENERIC_TOKENS
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


def _scope_prompt(state: dict, failed_tests: list[str]) -> str:
    sections = [
        f"Issue title: {state.get('issue_title') or ''}",
        f"Issue body:\n{str(state.get('issue_body') or '')[:4000]}",
        f"Root cause:\n{state.get('root_cause') or ''}",
        "Failing tests:\n" + "\n".join(f"- {test}" for test in failed_tests),
    ]

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
        sections.append("Failing test code:\n" + "\n".join(snippets))

    sections.append(
        "Return only test IDs that directly verify the reported issue. "
        "Exclude tests for unrelated pre-existing failures."
    )
    return "\n\n".join(sections)[:16_000]


def build_scope_service(llm: StructuredLLM | None) -> IssueScopeService | None:
    return IssueScopeService(llm) if llm is not None else None

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ash_agent.config import Settings
from ash_agent.integrations.llm import StructuredLLM
from ash_agent.intelligence.patch_scope import (
    ScopeViolation,
    audit_patch_scope,
    scope_files_for_state,
)
from ash_agent.intelligence.patching import PatchProposal, apply_edits
from ash_agent.sandbox.docker_manager import CommandResult, DockerSandbox
from ash_agent.sandbox.executor import (
    install_commands,
    parse_pytest,
    prepare_workspace,
)
from ash_agent.sandbox.policies import SandboxPolicy
from ash_agent.services.repository_service import Checkout, RepositoryService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a senior software engineer repairing a Python repository. "
    "Return a minimal set of exact search/replace edits. old_text must be copied verbatim "
    "from the provided file content and must occur exactly once. Keep the existing style. "
    "Only edit files and symbols listed in Allowed scope. Never edit tests and never fix "
    "failing tests that are not issue-scoped targets."
)


@dataclass(frozen=True)
class PatchOutcome:
    summary: str
    diff: str
    changed_files: list[str]
    files: dict[str, str]
    edits: list[dict]
    workspace_path: str


@dataclass(frozen=True)
class ValidationOutcome:
    passed: bool
    test_results: dict
    logs: str


class RepairService:
    def __init__(
        self,
        settings: Settings,
        llm: StructuredLLM,
        *,
        sandbox: DockerSandbox | None = None,
        cloner: RepositoryService | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.policy = SandboxPolicy.from_settings(settings)
        self.sandbox = sandbox or DockerSandbox(self.policy)
        self.cloner = cloner or RepositoryService(
            workspace_dir=settings.workspace_dir,
            base_url=settings.github_base_url,
            token=settings.github_token.get_secret_value(),
            timeout_seconds=settings.git_clone_timeout_seconds,
        )

    async def generate_patch(self, state: dict) -> PatchOutcome:
        checkout = await self._checkout(state)
        attempt = int(state.get("attempt", 0)) + 1
        workspace = (
            Path(self.settings.repair_workspace_dir)
            / _safe_name(str(state.get("run_id") or "run"))
            / f"attempt-{attempt}"
        )
        if await asyncio.to_thread(workspace.exists):
            await asyncio.to_thread(shutil.rmtree, workspace)
        await asyncio.to_thread(prepare_workspace, checkout.path, workspace)

        proposal = await self.llm.complete_structured(
            PatchProposal,
            SYSTEM_PROMPT,
            build_patch_prompt(state, checkout.path),
        )
        if not isinstance(proposal, PatchProposal):
            proposal = PatchProposal.model_validate(proposal)

        allowed_files = _scope_files(state)
        allowed_symbols = [str(symbol) for symbol in state.get("scope_symbols") or []]
        violations = audit_patch_scope(
            proposal.edits,
            checkout.path,
            allowed_files,
            allowed_symbols,
        )
        if violations:
            raise ScopeViolation("patch rejected by scope guard: " + "; ".join(violations))

        applied = await asyncio.to_thread(apply_edits, workspace, proposal)
        logger.info(
            "generated patch for %s attempt %s: %s",
            state.get("run_id"),
            attempt,
            applied.changed_files,
        )
        return PatchOutcome(
            summary=applied.summary,
            diff=applied.diff,
            changed_files=applied.changed_files,
            files=applied.files,
            edits=[edit.model_dump() for edit in proposal.edits],
            workspace_path=str(workspace),
        )

    async def validate(self, state: dict) -> ValidationOutcome:
        workspace_path = state.get("patch_workspace")
        if not workspace_path or not await asyncio.to_thread(Path(workspace_path).exists):
            return _failed_validation("patch workspace is missing", state)

        workspace = Path(workspace_path)
        install_results = []
        for command in install_commands(workspace, self.policy):
            result = await self.sandbox.run(
                command,
                workspace,
                network=self.policy.install_network,
                timeout_seconds=self.policy.install_timeout_seconds,
            )
            install_results.append(result)
            if result.exit_code != 0 or result.timed_out:
                break

        install_ok = all(
            result.exit_code == 0 and not result.timed_out for result in install_results
        )
        if not install_ok:
            return _failed_validation(
                "dependency installation failed",
                state,
                install_results=install_results,
            )

        python = _python_command(workspace)
        target_tests = _target_tests(state)
        unrelated_tests = _unrelated_tests(state, target_tests)
        focused: CommandResult | None = None
        if target_tests:
            focused = await self.sandbox.run(
                [
                    python,
                    "-m",
                    "pytest",
                    *target_tests,
                    "-q",
                    "--disable-warnings",
                    "--maxfail=5",
                ],
                workspace,
                network=self.policy.network,
                timeout_seconds=self.policy.timeout_seconds,
            )
        focused_ok = focused is None or (focused.exit_code == 0 and not focused.timed_out)

        full: CommandResult | None = None
        if focused_ok:
            full = await self.sandbox.run(
                [python, "-m", "pytest", *self.policy.test_args],
                workspace,
                network=self.policy.network,
                timeout_seconds=self.policy.timeout_seconds,
            )
        full_counts = parse_pytest(full.output) if full is not None else {}
        full_failures = set(full_counts.get("failed_tests") or [])
        new_failures = sorted(full_failures - set(unrelated_tests))
        full_ok = (
            full is not None
            and not full.timed_out
            and (
                full.exit_code == 0
                or (full.exit_code == 1 and bool(full_failures) and not new_failures)
            )
        )
        passed = install_ok and focused_ok and full_ok
        logger.info(
            "validation for %s attempt %s: passed=%s target=%s unrelated=%s",
            state.get("run_id"),
            state.get("attempt", 0),
            passed,
            target_tests,
            unrelated_tests,
        )

        sections = []
        for index, result in enumerate(install_results, start=1):
            sections.append(f"=== install {index}: {' '.join(result.command)} ===")
            sections.append(result.output.strip())
        if focused is not None:
            sections.append(f"=== focused: {' '.join(focused.command)} ===")
            sections.append(focused.output.strip())
        if full is not None:
            sections.append(f"=== full: {' '.join(full.command)} ===")
            sections.append(full.output.strip())
        logs = "\n".join(section for section in sections if section)[:50_000]

        return ValidationOutcome(
            passed=passed,
            test_results={
                "passed": passed,
                "attempt": state.get("attempt", 0),
                "install_ok": install_ok,
                "target_tests": target_tests,
                "unrelated_failed_tests": unrelated_tests,
                "new_failures": new_failures,
                "focused": _result_payload(focused),
                "full": _result_payload(full),
            },
            logs=logs,
        )

    async def _checkout(self, state: dict) -> Checkout:
        repository_path = state.get("repository_path")
        if repository_path and await asyncio.to_thread(Path(repository_path).exists):
            return Checkout(
                path=Path(repository_path),
                repository=str(state["repository"]),
                commit_sha=str(state.get("commit_sha") or "unknown"),
            )
        return await self.cloner.ensure_checkout(
            repository=str(state["repository"]),
            commit_sha=state.get("commit_sha"),
            ref=state.get("ref"),
        )


def cleanup_workspace(path: str | Path | None) -> None:
    if path:
        shutil.rmtree(Path(path), ignore_errors=True)


def _python_command(workspace: Path) -> str:
    venv_python = workspace / ".venv/bin/python"
    return ".venv/bin/python" if venv_python.exists() or venv_python.is_symlink() else "python"


def _failed_tests(state: dict) -> list[str]:
    reproduction = state.get("reproduction_results") or {}
    tests = reproduction.get("failed_tests") or []
    return [str(test) for test in tests if str(test).strip()]


def _target_tests(state: dict) -> list[str]:
    target = state.get("target_tests")
    if isinstance(target, list) and target:
        return [str(test) for test in target if str(test).strip()]
    return _failed_tests(state)


def _scope_files(state: dict) -> list[str]:
    return scope_files_for_state(state)


def _unrelated_tests(state: dict, target_tests: list[str]) -> list[str]:
    unrelated = state.get("unrelated_failed_tests")
    if isinstance(unrelated, list) and unrelated:
        return [str(test) for test in unrelated if str(test).strip()]
    target = set(target_tests)
    return [test for test in _failed_tests(state) if test not in target]


def _result_payload(result: CommandResult | None) -> dict:
    if result is None:
        return {"skipped": True}
    return {
        "command": " ".join(result.command),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_seconds": round(result.duration_seconds, 2),
        **parse_pytest(result.output),
    }


def _failed_validation(
    reason: str,
    state: dict,
    *,
    install_results: list[CommandResult] | None = None,
) -> ValidationOutcome:
    logs = [
        f"validation failed: {reason}",
        *[result.output for result in install_results or []],
    ]
    return ValidationOutcome(
        passed=False,
        test_results={
            "passed": False,
            "attempt": state.get("attempt", 0),
            "error": reason,
        },
        logs="\n".join(logs)[:50_000],
    )


def build_patch_prompt(state: dict, source_root: Path | None = None) -> str:
    sections = [
        f"Repository: {state.get('repository')}",
        f"Commit: {state.get('commit_sha') or 'unknown'}",
        f"Issue: {state.get('issue_title') or ''}",
        str(state.get("issue_body") or "")[:3000],
        f"Root cause: {state.get('root_cause') or ''}",
        f"Plan: {state.get('plan') or ''}",
    ]

    target_tests = _target_tests(state)
    unrelated_tests = _unrelated_tests(state, target_tests)
    if target_tests:
        sections.append(
            "Issue-scoped failing tests (fix ONLY these):\n"
            + "\n".join(f"- {test}" for test in target_tests)
        )
    if unrelated_tests:
        sections.append(
            "Unrelated pre-existing failures (DO NOT fix these):\n"
            + "\n".join(f"- {test}" for test in unrelated_tests)
        )

    allowed_files = _scope_files(state)
    if allowed_files:
        sections.append(
            "Allowed files (DO NOT edit anything else):\n"
            + "\n".join(f"- {path}" for path in allowed_files)
        )
    allowed_symbols = [str(symbol) for symbol in state.get("scope_symbols") or []]
    if allowed_symbols:
        sections.append(
            "Allowed symbols (DO NOT edit other functions/classes):\n"
            + "\n".join(f"- {symbol}" for symbol in allowed_symbols)
        )

    failure_logs = str(state.get("failure_logs") or "")
    if failure_logs:
        sections.append("Failure logs:\n" + failure_logs[:5000])

    files = _relevant_files(state, source_root)
    if files:
        sections.append(
            "Exact file contents:\n"
            + "\n\n".join(f"--- {path} ---\n{content}" for path, content in files)
        )
    else:
        chunks = state.get("retrieved_chunks") or []
        if chunks:
            context = []
            for chunk in chunks[:6]:
                context.append(
                    f"--- {chunk.get('path')}:{chunk.get('start_line')}-"
                    f"{chunk.get('end_line')} ---\n{str(chunk.get('content') or '')[:1500]}"
                )
            sections.append("Relevant code:\n" + "\n".join(context))

    validation_logs = str(state.get("validation_logs") or "")
    if validation_logs:
        sections.append("Previous validation failure:\n" + validation_logs[:3000])

    patch_error = str(state.get("patch_error") or "")
    if patch_error:
        sections.append("Previous patch error:\n" + patch_error[:2000])

    lessons = state.get("prior_lessons") or []
    if lessons:
        rendered = []
        for lesson in lessons[:3]:
            if isinstance(lesson, dict):
                rendered.append(
                    f"- {lesson.get('root_cause', '')}: {lesson.get('patch_summary', '')}"
                )
        if rendered:
            sections.append("Verified lessons for this repository:\n" + "\n".join(rendered))

    sections.append(
        "Fix only the issue-scoped tests and the reported issue. Do not change unrelated "
        "functions, tests, or fix unrelated pre-existing failures, even if they appear in the "
        "logs. Every edit must be inside Allowed files and Allowed symbols. Before returning, "
        "self-check that no other file, function, or line was touched. Return exact "
        "old_text/new_text edits and copy old_text from the file contents, including indentation."
    )
    return "\n\n".join(section for section in sections if section)[:40_000]


def _relevant_files(state: dict, source_root: Path | None) -> list[tuple[str, str]]:
    if source_root is None:
        return []

    paths: list[str] = []
    for path in state.get("suspected_files") or []:
        if isinstance(path, str):
            paths.append(path)
    for chunk in state.get("retrieved_chunks") or []:
        path = chunk.get("path") if isinstance(chunk, dict) else None
        if isinstance(path, str):
            paths.append(path)

    files: list[tuple[str, str]] = []
    total = 0
    for path in dict.fromkeys(paths):
        if not path.endswith(".py"):
            continue
        candidate = source_root / path
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(content) > 12_000:
            content = content[:12_000]
        if total + len(content) > 30_000:
            break
        files.append((path, content))
        total += len(content)
        if len(files) >= 5:
            break
    return files


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._") or "run"


def build_repair_service(settings: Settings, llm: StructuredLLM | None) -> RepairService | None:
    if not settings.repair_enabled or llm is None or not settings.sandbox_enabled:
        return None
    return RepairService(settings, llm)

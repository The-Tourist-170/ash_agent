from pathlib import Path

from pydantic import BaseModel

from ash_agent.config import Settings
from ash_agent.intelligence.patching import PatchEdit, PatchProposal
from ash_agent.sandbox.docker_manager import CommandResult
from ash_agent.services.repair_service import (
    RepairService,
    build_patch_prompt,
    cleanup_workspace,
)
from ash_agent.services.repository_service import Checkout


class FakeLLM:
    def __init__(self, proposal: PatchProposal) -> None:
        self.proposal = proposal
        self.prompts: list[str] = []

    async def complete_structured(
        self,
        schema: type[BaseModel],
        system_prompt: str,
        user_prompt: str,
    ) -> BaseModel:
        self.prompts.append(user_prompt)
        return self.proposal


class FakeSandbox:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = iter(results)
        self.calls = []

    async def run(self, command, workspace, *, network, timeout_seconds):
        self.calls.append((tuple(command), network))
        return next(self.results)


class FakeCloner:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def ensure_checkout(self, repository, commit_sha=None, ref=None) -> Checkout:
        return Checkout(
            path=self.root,
            repository=repository,
            commit_sha=commit_sha or "a" * 40,
        )


def result(exit_code: int, stdout: str = "") -> CommandResult:
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        timed_out=False,
        duration_seconds=0.2,
        command=("python", "-m", "pytest"),
    )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        sandbox_enabled=True,
        repair_enabled=True,
        repair_workspace_dir=str(tmp_path / "repairs"),
        sandbox_workspace_dir=str(tmp_path / "sandboxes"),
        sandbox_install_dependencies=False,
    )


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "demo").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "demo" / "core.py").write_text(
        "def total(subtotal, discount, tax):\n    return subtotal + subtotal * tax - discount\n"
    )
    (root / "tests" / "test_core.py").write_text("def test_total():\n    assert True\n")
    return root


def state(root: Path) -> dict:
    return {
        "run_id": "repair-run",
        "repository": "owner/repo",
        "commit_sha": "a" * 40,
        "repository_path": str(root),
        "suspected_files": ["src/demo/core.py"],
        "retrieved_chunks": [
            {
                "path": "src/demo/core.py",
                "start_line": 1,
                "end_line": 2,
                "content": (
                    "def total(subtotal, discount, tax):\n"
                    "    return subtotal + subtotal * tax - discount\n"
                ),
            }
        ],
        "reproduction_results": {"failed_tests": ["tests/test_core.py::test_total"]},
        "failure_logs": "assert 100.0 == 99.0",
        "attempt": 0,
        "patch_error": None,
        "validation_logs": "",
    }


class TestPatchGeneration:
    async def test_generates_patch_from_exact_file_content(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        proposal = PatchProposal(
            summary="apply discount before tax",
            edits=[
                PatchEdit(
                    path="src/demo/core.py",
                    old_text="return subtotal + subtotal * tax - discount",
                    new_text="net = subtotal - discount\n    return net + net * tax",
                    reason="discount first",
                )
            ],
        )
        llm = FakeLLM(proposal)
        service = RepairService(
            settings(tmp_path),
            llm,
            sandbox=FakeSandbox([]),
            cloner=FakeCloner(root),
        )

        outcome = await service.generate_patch(state(root))

        assert outcome.changed_files == ["src/demo/core.py"]
        assert "net = subtotal - discount" in outcome.files["src/demo/core.py"]
        assert "--- a/src/demo/core.py" in outcome.diff
        assert "Exact file contents" in llm.prompts[0]
        assert "tests/test_core.py::test_total" in llm.prompts[0]


class TestValidation:
    async def test_passes_focused_and_full_suite(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        sandbox = FakeSandbox(
            [
                result(0, "3 passed"),
                result(0, "6 passed"),
            ]
        )
        service = RepairService(
            settings(tmp_path), FakeLLM(PatchProposal(summary="x", edits=[])), sandbox=sandbox
        )
        patch_state = state(root)
        patch_state["patch_workspace"] = str(root)

        outcome = await service.validate(patch_state)

        assert outcome.passed is True
        assert outcome.test_results["focused"]["passed"] == 3
        assert outcome.test_results["full"]["passed"] == 6
        assert [call[1] for call in sandbox.calls] == ["none", "none"]

    async def test_failed_focused_tests_skip_full_suite(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        sandbox = FakeSandbox([result(1, "1 failed, 2 passed")])
        service = RepairService(
            settings(tmp_path), FakeLLM(PatchProposal(summary="x", edits=[])), sandbox=sandbox
        )
        patch_state = state(root)
        patch_state["patch_workspace"] = str(root)

        outcome = await service.validate(patch_state)

        assert outcome.passed is False
        assert outcome.test_results["full"] == {"skipped": True}
        assert len(sandbox.calls) == 1

    async def test_missing_workspace_fails(self, tmp_path: Path) -> None:
        service = RepairService(
            settings(tmp_path),
            FakeLLM(PatchProposal(summary="x", edits=[])),
            sandbox=FakeSandbox([]),
        )

        outcome = await service.validate({"attempt": 1, "patch_workspace": None})

        assert outcome.passed is False
        assert "missing" in outcome.test_results["error"]


class TestPrompt:
    def test_includes_failure_logs_and_lessons(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        prompt = build_patch_prompt(
            {
                **state(root),
                "prior_lessons": [{"root_cause": "tax order", "patch_summary": "discount first"}],
            },
            root,
        )

        assert "assert 100.0 == 99.0" in prompt
        assert "tax order" in prompt
        assert "def total" in prompt


class TestCleanup:
    def test_removes_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "work"
        workspace.mkdir()
        (workspace / "file").write_text("x")

        cleanup_workspace(workspace)

        assert not workspace.exists()

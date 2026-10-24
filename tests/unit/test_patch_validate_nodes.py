from ash_agent.agents.nodes.patch import make_patch_node
from ash_agent.agents.nodes.validate import make_validate_node
from ash_agent.services.repair_service import PatchOutcome, ValidationOutcome


def state() -> dict:
    return {
        "run_id": "node-run",
        "repository": "owner/repo",
        "attempt": 0,
        "patch_error": None,
    }


class FakeRepair:
    def __init__(self, patch_outcome=None, validation_outcome=None, error=None):
        self.patch_outcome = patch_outcome
        self.validation_outcome = validation_outcome
        self.error = error

    async def generate_patch(self, state):
        if self.error:
            raise self.error
        return self.patch_outcome

    async def validate(self, state):
        if self.error:
            raise self.error
        return self.validation_outcome


def patch_outcome() -> PatchOutcome:
    return PatchOutcome(
        summary="fix tax order",
        diff="--- a/core.py\n+++ b/core.py\n",
        changed_files=["core.py"],
        files={"core.py": "fixed\n"},
        edits=[{"path": "core.py"}],
        workspace_path="/tmp/work",
    )


def validation_outcome(passed: bool) -> ValidationOutcome:
    return ValidationOutcome(
        passed=passed,
        test_results={"passed": passed, "attempt": 1},
        logs="tests output",
    )


class TestPatchNode:
    async def test_maps_patch_outcome(self) -> None:
        result = await make_patch_node(FakeRepair(patch_outcome=patch_outcome()))(state())

        assert result["attempt"] == 1
        assert result["patch_summary"] == "fix tax order"
        assert result["changed_files"] == ["core.py"]
        assert result["patch_workspace"] == "/tmp/work"
        assert result["status"] == "validating"

    async def test_patch_error_sets_failed_test_result(self) -> None:
        result = await make_patch_node(FakeRepair(error=RuntimeError("old_text missing")))(state())

        assert result["attempt"] == 1
        assert result["test_results"]["passed"] is False
        assert "old_text missing" in result["patch_error"]

    async def test_mock_patch_unchanged(self) -> None:
        result = await make_patch_node(None)(state())

        assert result["attempt"] == 1
        assert "mock patch" in result["patch_diff"]


class TestValidateNode:
    async def test_real_validation_outcome(self) -> None:
        node = make_validate_node(1, FakeRepair(validation_outcome=validation_outcome(True)))

        result = await node({**state(), "attempt": 1})

        assert result["test_results"]["passed"] is True
        assert result["validation_logs"] == "tests output"

    async def test_patch_error_skips_sandbox(self) -> None:
        node = make_validate_node(1, FakeRepair(validation_outcome=validation_outcome(True)))

        result = await node({**state(), "patch_error": "bad edit"})

        assert result["test_results"]["passed"] is False
        assert result["validation_logs"] == "bad edit"

    async def test_mock_validation_unchanged(self) -> None:
        node = make_validate_node(1)

        result = await node({**state(), "attempt": 1})

        assert result["test_results"]["passed"] is True
        assert result["test_results"]["summary"] == "mock tests passed"

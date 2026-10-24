from ash_agent.agents.nodes.reproduce import (
    make_reproduce_node,
    make_runtime_reproduce_node,
)
from ash_agent.agents.routing import route_after_reproduce, route_after_runtime
from ash_agent.sandbox.executor import ReproductionOutcome, RuntimeOutcome


def base_state() -> dict:
    return {
        "run_id": "reproduce-node",
        "repository": "The-Tourist-170/dummy_project",
        "commit_sha": "a" * 40,
        "repository_path": "/tmp/checkout",
    }


class FakeReproduction:
    def __init__(self, outcome: ReproductionOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome
        self.error = error

    async def reproduce(self, state) -> ReproductionOutcome:
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


def outcome(confirmed: bool) -> ReproductionOutcome:
    return ReproductionOutcome(
        confirmed=confirmed,
        logs="1 failed, 3 passed",
        test_results={"exit_code": 1, "failed": 1, "passed": 3},
        backend="docker",
    )


class TestReproduceNode:
    async def test_maps_sandbox_evidence_into_state(self) -> None:
        result = await make_reproduce_node(FakeReproduction(outcome(True)))(base_state())

        assert result["reproduction_confirmed"] is True
        assert result["failure_logs"] == "1 failed, 3 passed"
        assert result["reproduction_results"]["failed"] == 1
        assert result["status"] == "reproducing"

    async def test_sandbox_error_is_not_confirmed(self) -> None:
        result = await make_reproduce_node(
            FakeReproduction(error=RuntimeError("docker unavailable"))
        )(base_state())

        assert result["reproduction_confirmed"] is False
        assert result["reproduction_results"]["sandbox_error"] == "RuntimeError"
        assert "docker unavailable" in result["failure_logs"]

    async def test_default_mock_confirms(self) -> None:
        result = await make_reproduce_node(None)(base_state())

        assert result["reproduction_confirmed"] is True


class FakeRuntime:
    def __init__(self, outcome: RuntimeOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome
        self.error = error

    async def reproduce_runtime(self, state) -> RuntimeOutcome:
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


def runtime_outcome(confirmed: bool) -> RuntimeOutcome:
    return RuntimeOutcome(
        confirmed=confirmed,
        logs="NameError: message is not defined",
        results={"mode": "command", "confirmed": confirmed},
        backend="docker",
    )


class TestReproduceRouting:
    def test_confirmed_goes_to_scope(self) -> None:
        assert route_after_reproduce({"reproduction_confirmed": True}) == "scope"

    def test_unconfirmed_goes_to_escalate(self) -> None:
        assert route_after_reproduce({"reproduction_confirmed": False}) == "escalate"

    def test_issue_without_pytest_failure_still_scopes_runtime(self) -> None:
        assert (
            route_after_reproduce({"reproduction_confirmed": False, "source": "issue"}) == "scope"
        )


class TestRuntimeReproduceNode:
    async def test_maps_runtime_evidence(self) -> None:
        result = await make_runtime_reproduce_node(FakeRuntime(runtime_outcome(True)))(base_state())

        assert result["reproduction_confirmed"] is True
        assert result["runtime_reproduction"]["mode"] == "command"
        assert "NameError" in result["failure_logs"]

    async def test_runtime_error_is_not_confirmed(self) -> None:
        result = await make_runtime_reproduce_node(
            FakeRuntime(error=RuntimeError("docker unavailable"))
        )(base_state())

        assert result["reproduction_confirmed"] is False
        assert result["runtime_reproduction"]["sandbox_error"] == "RuntimeError"


class TestRuntimeRouting:
    def test_runtime_failure_goes_to_plan(self) -> None:
        assert route_after_runtime({"reproduction_confirmed": True}) == "plan"

    def test_runtime_pass_escalates(self) -> None:
        assert route_after_runtime({"reproduction_confirmed": False}) == "escalate"

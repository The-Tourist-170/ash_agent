from uuid import uuid4

from ash_agent.agents.graph import build_graph
from ash_agent.config import Settings
from ash_agent.persistence.checkpoints import open_checkpointer


def initial_state(thread_id: str) -> dict:
    return {
        "run_id": thread_id,
        "source": "issue",
        "repository": "The-Tourist-170/dummy_project",
        "ref": "main",
        "commit_sha": None,
        "issue_number": 7,
        "issue_title": "[BUG] Calculate tax",
        "issue_body": "Totals differ by one cent.",
        "attempt": 0,
        "max_attempts": 3,
    }


class TestCheckpointResumption:
    async def test_graph_resumes_after_interruption(self, test_settings: Settings) -> None:
        thread_id = f"resume-{uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        state = initial_state(thread_id)

        async with open_checkpointer(test_settings) as saver:
            interrupted_graph = build_graph(
                success_on_attempt=1,
                checkpointer=saver,
                interrupt_after=["reproduce"],
            )
            interrupted = await interrupted_graph.ainvoke(state, config)

            assert interrupted["status"] == "reproducing"
            assert interrupted["root_cause"].startswith("mock diagnosis")
            assert "plan" not in interrupted

            checkpoint = await saver.aget_tuple(config)
            assert checkpoint is not None

            resumed_graph = build_graph(success_on_attempt=1, checkpointer=saver)
            resumed = await resumed_graph.ainvoke(None, config)

        assert resumed["status"] == "passed"
        assert resumed["attempt"] == 1
        assert resumed["root_cause"] == interrupted["root_cause"]
        assert resumed["reproduction_confirmed"] is True

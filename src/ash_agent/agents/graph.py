from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ash_agent.agents.nodes.diagnose import diagnose
from ash_agent.agents.nodes.escalate import escalate
from ash_agent.agents.nodes.ingest import ingest
from ash_agent.agents.nodes.patch import patch
from ash_agent.agents.nodes.plan import plan
from ash_agent.agents.nodes.publish import publish
from ash_agent.agents.nodes.reflect import reflect
from ash_agent.agents.nodes.reproduce import reproduce
from ash_agent.agents.nodes.retrieve import retrieve
from ash_agent.agents.nodes.validate import make_validate_node
from ash_agent.agents.routing import route_after_validation
from ash_agent.agents.state import AgentState


def build_graph(
    *,
    success_on_attempt: int = 1,
    checkpointer: BaseCheckpointSaver | None = None,
    interrupt_after: list[str] | None = None,
):
    builder = StateGraph(AgentState)
    builder.add_node("ingest", ingest)
    builder.add_node("diagnose", diagnose)
    builder.add_node("retrieve", retrieve)
    builder.add_node("reproduce", reproduce)
    builder.add_node("plan", plan)
    builder.add_node("patch", patch)
    builder.add_node("validate", make_validate_node(success_on_attempt))
    builder.add_node("reflect", reflect)
    builder.add_node("publish", publish)
    builder.add_node("escalate", escalate)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "diagnose")
    builder.add_edge("diagnose", "retrieve")
    builder.add_edge("retrieve", "reproduce")
    builder.add_edge("reproduce", "plan")
    builder.add_edge("plan", "patch")
    builder.add_edge("patch", "validate")
    builder.add_conditional_edges("validate", route_after_validation)
    builder.add_edge("reflect", "plan")
    builder.add_edge("publish", END)
    builder.add_edge("escalate", END)

    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)

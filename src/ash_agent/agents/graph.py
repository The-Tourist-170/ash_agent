from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ash_agent.agents.nodes.diagnose import diagnose, make_llm_diagnose_node
from ash_agent.agents.nodes.escalate import escalate
from ash_agent.agents.nodes.ingest import ingest
from ash_agent.agents.nodes.patch import RepairBackend, make_patch_node
from ash_agent.agents.nodes.plan import plan
from ash_agent.agents.nodes.publish import PublishBackend, make_publish_node
from ash_agent.agents.nodes.reflect import reflect
from ash_agent.agents.nodes.reproduce import ReproductionBackend, make_reproduce_node
from ash_agent.agents.nodes.retrieve import RetrievalBackend, make_retrieve_node
from ash_agent.agents.nodes.validate import make_validate_node
from ash_agent.agents.routing import route_after_reproduce, route_after_validation
from ash_agent.agents.state import AgentState
from ash_agent.integrations.llm import DiagnosisLLM


def build_graph(
    *,
    success_on_attempt: int = 1,
    checkpointer: BaseCheckpointSaver | None = None,
    interrupt_after: list[str] | None = None,
    llm_client: DiagnosisLLM | None = None,
    retrieval_service: RetrievalBackend | None = None,
    reproduction_service: ReproductionBackend | None = None,
    repair_service: RepairBackend | None = None,
    publish_service: PublishBackend | None = None,
):
    diagnose_node = make_llm_diagnose_node(llm_client) if llm_client else diagnose
    retrieve_node = make_retrieve_node(retrieval_service)
    reproduce_node = make_reproduce_node(reproduction_service)
    patch_node = make_patch_node(repair_service)
    validate_node = make_validate_node(success_on_attempt, repair_service)
    publish_node = make_publish_node(publish_service)
    builder = StateGraph(AgentState)
    builder.add_node("ingest", ingest)
    builder.add_node("diagnose", diagnose_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("reproduce", reproduce_node)
    builder.add_node("plan", plan)
    builder.add_node("patch", patch_node)
    builder.add_node("validate", validate_node)
    builder.add_node("reflect", reflect)
    builder.add_node("publish", publish_node)
    builder.add_node("escalate", escalate)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "diagnose")
    builder.add_edge("diagnose", "retrieve")
    builder.add_edge("retrieve", "reproduce")
    builder.add_conditional_edges("reproduce", route_after_reproduce)
    builder.add_edge("plan", "patch")
    builder.add_edge("patch", "validate")
    builder.add_conditional_edges("validate", route_after_validation)
    builder.add_edge("reflect", "plan")
    builder.add_edge("publish", END)
    builder.add_edge("escalate", END)

    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)

import logging

from ash_agent.agents.state import AgentState
from ash_agent.integrations.llm import JSONLLM, LLMError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a senior software engineer diagnosing a failing repository. "
    "Respond with a single JSON object only, no markdown. The object must have: "
    "root_cause (string), suspected_files (array of repository-relative paths), "
    "plan (string). Keep each value short and concrete."
)


def build_user_prompt(state: AgentState) -> str:
    if state.get("source") == "issue":
        trigger = (
            f"GitHub issue #{state.get('issue_number')}: {state.get('issue_title') or ''}\n"
            f"Issue body:\n{(state.get('issue_body') or '')[:4000]}"
        )
    else:
        trigger = f"Failing CI workflow at commit {state.get('commit_sha') or 'unknown'}"

    return (
        f"Repository: {state.get('repository')}\n"
        f"Branch: {state.get('ref') or 'unknown'}\n"
        f"{trigger}\n\n"
        "Return JSON with root_cause, suspected_files and plan."
    )


def make_llm_diagnose_node(client: JSONLLM):
    async def diagnose_with_llm(state: AgentState) -> dict:
        try:
            data = await client.complete_json(SYSTEM_PROMPT, build_user_prompt(state))
        except LLMError as exc:
            logger.warning("LLM diagnosis failed, using mock fallback: %s", exc)
            return _mock_fallback(state)
        except Exception as exc:
            logger.warning("LLM diagnosis failed, using mock fallback: %s", type(exc).__name__)
            return _mock_fallback(state)

        root_cause = str(data.get("root_cause") or "").strip()
        if not root_cause:
            return _mock_fallback(state)

        update: dict = {
            "root_cause": root_cause,
            "status": "diagnosing",
        }
        suspected_files = data.get("suspected_files")
        if isinstance(suspected_files, list):
            files = [str(path).strip() for path in suspected_files if str(path).strip()]
            if files:
                update["suspected_files"] = files

        plan_value = data.get("plan")
        if isinstance(plan_value, list):
            plan = "; ".join(str(item).strip() for item in plan_value if str(item).strip())
        else:
            plan = str(plan_value or "").strip()
        if plan:
            update["plan"] = plan

        logger.info("LLM diagnosis completed for run %s", state.get("run_id"))
        return update

    return diagnose_with_llm


def _mock_fallback(state: AgentState) -> dict:
    if state.get("source") == "issue":
        title = state.get("issue_title") or "reported defect"
        subject = f"issue #{state.get('issue_number')}: {title}"
    else:
        subject = f"CI failure at {state.get('commit_sha') or 'unknown commit'}"
    return {
        "root_cause": f"mock diagnosis: {subject}",
        "status": "diagnosing",
    }


async def diagnose(state: AgentState) -> dict:
    return _mock_fallback(state)

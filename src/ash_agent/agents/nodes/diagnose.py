import logging

from ash_agent.agents.state import AgentState
from ash_agent.integrations.llm import DiagnosisLLM, LLMError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a senior software engineer diagnosing a failing repository. "
    "Identify the most likely root cause, the files that need inspection, and a concrete repair "
    "plan. Be concise and specific; do not invent file contents."
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
        f"{trigger}\n\n" + _lessons_prompt(state) + "Diagnose this failure."
    )


def _lessons_prompt(state: AgentState) -> str:
    lessons = state.get("prior_lessons") or []
    rendered = []
    for lesson in lessons[:3]:
        if isinstance(lesson, dict):
            rendered.append(
                f"- {lesson.get('root_cause', '')} -> {lesson.get('patch_summary', '')}"
            )
    if not rendered:
        return ""
    return "Verified lessons for this repository:\n" + "\n".join(rendered) + "\n\n"


def make_llm_diagnose_node(client: DiagnosisLLM):
    async def diagnose_with_llm(state: AgentState) -> dict:
        try:
            diagnosis = await client.diagnose(SYSTEM_PROMPT, build_user_prompt(state))
        except LLMError as exc:
            logger.warning("LLM diagnosis failed, using mock fallback: %s", exc)
            return _mock_fallback(state)
        except Exception as exc:
            logger.warning("LLM diagnosis failed, using mock fallback: %s", type(exc).__name__)
            return _mock_fallback(state)

        root_cause = diagnosis.root_cause.strip()
        if not root_cause:
            return _mock_fallback(state)

        update: dict = {
            "root_cause": root_cause,
            "status": "diagnosing",
        }
        files = [path.strip() for path in diagnosis.suspected_files if path.strip()]
        if files:
            update["suspected_files"] = files

        plan = diagnosis.plan.strip()
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

from typing import TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    source: str
    repository: str
    ref: str | None
    commit_sha: str | None
    issue_number: int | None
    issue_title: str | None
    issue_body: str | None
    failure_logs: str
    suspected_files: list[str]
    repository_path: str | None
    retrieved_chunks: list[dict]
    retrieval_backend: str | None
    root_cause: str | None
    reproduction_confirmed: bool
    reproduction_results: dict[str, object]
    plan: str | None
    patch_diff: str | None
    patch_summary: str | None
    patch_edits: list[dict]
    changed_files: list[str]
    patched_files: dict[str, str]
    patch_workspace: str | None
    patch_error: str | None
    validation_logs: str
    prior_lessons: list[dict]
    test_results: dict[str, object]
    attempt: int
    max_attempts: int
    status: str
    last_error: str | None

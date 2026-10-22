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
    plan: str | None
    patch_diff: str | None
    test_results: dict[str, bool | int | str]
    attempt: int
    max_attempts: int
    status: str
    last_error: str | None

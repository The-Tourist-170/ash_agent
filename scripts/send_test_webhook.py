#!/usr/bin/env python3
"""Send a signed GitHub workflow_run payload to a running ash-agent instance.

Examples:
    uv run python scripts/send_test_webhook.py

    uv run python scripts/send_test_webhook.py \
        --repo octo/demo --conclusion failure --branch main

    uv run python scripts/send_test_webhook.py \
        --url https://abc123.trycloudflare.com/webhooks/github \
        --delivery-id my-custom-delivery
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_payload(
    repository: str,
    commit_sha: str,
    branch: str,
    conclusion: str,
    workflow_run_id: int,
) -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": repository},
        "workflow_run": {
            "id": workflow_run_id,
            "name": "CI",
            "head_branch": branch,
            "head_sha": commit_sha,
            "conclusion": conclusion,
            "html_url": f"https://github.com/{repository}/actions/runs/{workflow_run_id}",
        },
    }


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def parse_args() -> argparse.Namespace:
    env_file = load_dotenv(PROJECT_ROOT / ".env")
    secret_default = os.getenv("GITHUB_WEBHOOK_SECRET") or env_file.get("GITHUB_WEBHOOK_SECRET", "")
    api_port = os.getenv("API_PORT") or env_file.get("API_PORT", "8000")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=f"http://127.0.0.1:{api_port}/webhooks/github",
        help="Webhook endpoint URL",
    )
    parser.add_argument("--secret", default=secret_default, help="Webhook HMAC secret")
    parser.add_argument("--repo", default="octo/demo", help="owner/name")
    parser.add_argument("--sha", default="b" * 40, help="Commit SHA to pin the run to")
    parser.add_argument("--branch", default="main", help="Branch name")
    parser.add_argument(
        "--conclusion",
        default="failure",
        choices=["failure", "timed_out", "startup_failure", "success", "cancelled"],
        help="Workflow conclusion",
    )
    parser.add_argument(
        "--delivery-id",
        default=None,
        help="X-GitHub-Delivery value (default: random UUID)",
    )
    parser.add_argument(
        "--workflow-run-id",
        type=int,
        default=123456789,
        help="Numeric workflow run id",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.secret:
        print("error: webhook secret is empty (set GITHUB_WEBHOOK_SECRET in .env)", file=sys.stderr)
        return 2

    payload = build_payload(args.repo, args.sha, args.branch, args.conclusion, args.workflow_run_id)
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    delivery_id = args.delivery_id or str(uuid.uuid4())

    http_request = request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sign(args.secret, body),
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": "workflow_run",
        },
    )

    try:
        with request.urlopen(http_request, timeout=10) as response:
            status_code = response.status
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        status_code = exc.code
        response_body = exc.read().decode("utf-8")
    except error.URLError as exc:
        print(f"connection failed: {exc.reason}", file=sys.stderr)
        print("is the API running? try: uv run ash-agent", file=sys.stderr)
        return 1

    print(f"delivery: {delivery_id}")
    print(f"status:   {status_code}")
    print(f"response: {response_body}")
    return 0 if status_code == 202 else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Clone a repository and reproduce its failing tests in the Docker sandbox.

Usage:
    uv run python scripts/reproduce_repository.py \
        --repository The-Tourist-170/dummy_project
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ash_agent.config import get_settings
from ash_agent.sandbox.executor import build_reproduction_service
from ash_agent.services.repository_service import RepositoryService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="The-Tourist-170/dummy_project")
    parser.add_argument("--commit", default=None)
    parser.add_argument("--ref", default="main")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    service = build_reproduction_service(settings)
    if service is None:
        raise SystemExit("sandbox is disabled (set SANDBOX_ENABLED=true)")

    cloner = RepositoryService(
        workspace_dir=settings.workspace_dir,
        base_url=settings.github_base_url,
        token=settings.github_token.get_secret_value(),
        timeout_seconds=settings.git_clone_timeout_seconds,
    )
    checkout = await cloner.ensure_checkout(args.repository, args.commit, args.ref)

    outcome = await service.reproduce(
        {
            "run_id": "reproduce-script",
            "repository": checkout.repository,
            "commit_sha": checkout.commit_sha,
            "repository_path": str(checkout.path),
        }
    )

    print(
        json.dumps(
            {
                "repository": checkout.repository,
                "commit_sha": checkout.commit_sha,
                "checkout": str(Path(checkout.path)),
                "backend": outcome.backend,
                "confirmed": outcome.confirmed,
                "test_results": outcome.test_results,
            },
            indent=2,
        )
    )
    print("\n--- logs ---")
    print(outcome.logs)


if __name__ == "__main__":
    asyncio.run(main())

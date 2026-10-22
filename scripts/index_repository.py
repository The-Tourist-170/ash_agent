#!/usr/bin/env python3
"""Clone, index and query a repository through the Phase 3 pipeline.

Usage:
    uv run python scripts/index_repository.py \
        --query "calculate_total tax discount" \
        --repository The-Tourist-170/dummy_project
"""

from __future__ import annotations

import argparse
import asyncio
import json

from ash_agent.config import get_settings
from ash_agent.persistence.neo4j import Neo4jCodeGraph
from ash_agent.persistence.qdrant import QdrantCodeIndex
from ash_agent.services.retrieval_service import build_retrieval_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="The-Tourist-170/dummy_project")
    parser.add_argument("--commit", default=None)
    parser.add_argument("--ref", default="main")
    parser.add_argument(
        "--query",
        default="calculate_total applies tax before the discount; expected 99.0 but got 100.0",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    service = build_retrieval_service(settings)
    if service is None:
        raise SystemExit("retrieval is disabled (set RETRIEVAL_ENABLED=true)")

    state = {
        "run_id": "index-script",
        "repository": args.repository,
        "ref": args.ref,
        "commit_sha": args.commit,
        "issue_title": args.query,
        "issue_body": "",
        "root_cause": None,
        "plan": None,
        "suspected_files": [],
    }
    outcome = await service.retrieve(state)

    print(
        json.dumps(
            {
                "repository": outcome.repository,
                "commit_sha": outcome.commit_sha,
                "checkout": outcome.checkout_path,
                "backend": outcome.backend,
                "graph_indexed": outcome.graph_indexed,
                "files": outcome.files,
            },
            indent=2,
        )
    )

    print("\nTop chunks:")
    for chunk in outcome.chunks:
        print(
            f"- {chunk['path']}:{chunk['start_line']}-{chunk['end_line']} "
            f"score={chunk['score']} sources={chunk['sources']} symbols={chunk['symbols']}"
        )

    async with Neo4jCodeGraph(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password.get_secret_value(),
        database=settings.neo4j_database,
    ) as graph:
        print(
            "\nNeo4j:",
            json.dumps(await graph.summary(outcome.repository, outcome.commit_sha)),
        )

    async with QdrantCodeIndex(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value(),
        collection=settings.qdrant_collection,
        dimension=settings.embedding_dimension,
    ) as index:
        print(
            "Qdrant points:",
            await index.count(outcome.repository, outcome.commit_sha),
        )


if __name__ == "__main__":
    asyncio.run(main())

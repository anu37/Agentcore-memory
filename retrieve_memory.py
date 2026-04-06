"""
Retrieve context from a project's AgentCore Memory.

Usage:
    python retrieve_memory.py --project "sus lens" --query "observability budget decision"
    python retrieve_memory.py --project "sus lens" --query "action items" --top-k 10
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

from ingest_transcript import get_registry, lookup_memory_id

AWS_REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")


def retrieve(project_key: str, query: str, top_k: int = 5, region: str = AWS_REGION) -> list[dict]:
    """
    Return the top_k most relevant speaker turns for the query within a project.

    Each result:
        {
            "content":   str,    # "[Speaker]: text"
            "score":     float,
            "sessionId": str,    # meeting_id the turn came from
        }
    """
    registry = get_registry(region=region)
    memory_id = lookup_memory_id(registry, project_key)
    if not memory_id:
        raise ValueError(f"No memory registered for project '{project_key}'. "
                         "Run ingest_transcript.py first.")

    client = boto3.client("bedrock-agentcore", region_name=region)
    response = client.retrieve_memories(
        memoryId=memory_id,
        query=query,
        maxResults=top_k,
    )

    results = []
    for record in response.get("memoryRecords", []):
        content = (
            record.get("content", {})
            .get("conversationEvent", {})
            .get("content", [{}])[0]
            .get("text", "")
        )
        results.append(
            {
                "content": content,
                "score": record.get("score"),
                "sessionId": record.get("sessionId"),
            }
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Retrieve context from a project's AgentCore Memory.")
    parser.add_argument("--project", required=True, help="Project key (e.g. 'sus lens')")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--region", default=AWS_REGION)
    args = parser.parse_args()

    try:
        results = retrieve(args.project, args.query, top_k=args.top_k, region=args.region)
    except (ValueError, ClientError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] score={r['score']:.4f}  meeting={r['sessionId']}")
        print(f"     {r['content']}")


if __name__ == "__main__":
    main()

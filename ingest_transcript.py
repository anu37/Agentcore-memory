"""
Stand-up transcript ingestion into AWS AgentCore Memory.
One AgentCore Memory resource is created per project and tracked in DynamoDB.

Usage:
    # Ingest a transcript (creates memory resource for new projects automatically)
    python ingest_transcript.py --transcript path/to/transcript.json

    # List all project → memory ID mappings
    python ingest_transcript.py --list-projects

Required environment variables (or edit defaults below):
    MEMORY_REGISTRY_TABLE   — DynamoDB table name for project→memory mappings
    AWS_REGION_NAME         — AWS region (default: us-east-1)
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Config — edit these or pass via environment variables
# ---------------------------------------------------------------------------
import os

AWS_REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")
MEMORY_REGISTRY_TABLE = os.environ.get("MEMORY_REGISTRY_TABLE", "agentcore-memory-registry")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def get_agentcore_client(region: str = AWS_REGION):
    return boto3.client("bedrock-agentcore", region_name=region)


def get_registry(region: str = AWS_REGION):
    dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(MEMORY_REGISTRY_TABLE)


# ---------------------------------------------------------------------------
# Project registry — DynamoDB: { project_key (PK), memory_id, created_at }
# ---------------------------------------------------------------------------
def lookup_memory_id(registry, project_key: str) -> str | None:
    """Return the memory_id for a project, or None if not registered yet."""
    response = registry.get_item(Key={"project_key": project_key})
    item = response.get("Item")
    return item["memory_id"] if item else None


def register_memory_id(registry, project_key: str, memory_id: str) -> None:
    """Persist the project → memory_id mapping."""
    registry.put_item(
        Item={
            "project_key": project_key,
            "memory_id": memory_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def resolve_memory_id(agentcore_client, registry, project_key: str) -> str:
    """
    Return the existing memory_id for the project, or create a new
    AgentCore Memory resource and register it if this is a new project.
    """
    memory_id = lookup_memory_id(registry, project_key)
    if memory_id:
        print(f"  Found existing memory for project '{project_key}': {memory_id}")
        return memory_id

    print(f"  No memory found for project '{project_key}' — creating one...")
    memory_id = create_memory(agentcore_client, project_key)
    register_memory_id(registry, project_key, memory_id)
    print(f"  Registered: '{project_key}' → {memory_id}")
    return memory_id


# ---------------------------------------------------------------------------
# AgentCore Memory resource creation
# ---------------------------------------------------------------------------
def create_memory(client, project_key: str) -> str:
    """Create a per-project AgentCore Memory resource and return its memoryId."""
    safe_name = project_key.replace(" ", "-").lower()
    response = client.create_memory(
        name=f"transcripts-{safe_name}",
        description=f"Meeting transcripts for project: {project_key}",
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "speaker-turns",
                    "description": "Stores individual speaker turns from meeting transcripts.",
                }
            }
        ],
    )
    memory_id = response["memory"]["memoryId"]
    print(f"  Created memory ID: {memory_id}")
    return memory_id


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------
def parse_transcript(transcript: dict) -> list[dict]:
    """
    Convert transcript JSON into a flat list of speaker turns.

    Each turn:
        {
            "speaker":   str,
            "text":      str,       # all sub-segments joined
            "start":     int,       # seconds from meeting start
            "end":       int,
            "timestamp": datetime,  # absolute UTC time
        }
    """
    meta = transcript.get("metadata", {})
    meeting_start_str = meta.get("started_at", "")

    try:
        meeting_start = datetime.fromisoformat(meeting_start_str.replace("Z", "+00:00"))
    except ValueError:
        meeting_start = datetime.now(timezone.utc)
        print(f"  Warning: could not parse started_at '{meeting_start_str}', using now().")

    turns = []
    for block in transcript.get("segments", []):
        speaker = block.get("speaker", "Unknown")
        segs = block.get("segments", [])
        if not segs:
            continue

        full_text = " ".join(s["text"].strip() for s in segs if s.get("text"))
        start_sec = segs[0].get("start", 0)
        end_sec = segs[-1].get("end", start_sec)
        abs_timestamp = meeting_start + timedelta(seconds=start_sec)

        turns.append(
            {
                "speaker": speaker,
                "text": full_text,
                "start": start_sec,
                "end": end_sec,
                "timestamp": abs_timestamp,
            }
        )

    print(f"  Parsed {len(turns)} speaker turns.")
    return turns


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def build_conversation_events(turns: list[dict]) -> list[dict]:
    """
    Map speaker turns to AgentCore conversationEvent payloads.
    Text is prefixed with "[Speaker Name]:" so retrieved chunks are self-contained.
    """
    events = []
    for turn in turns:
        events.append(
            {
                "timestamp": turn["timestamp"].isoformat(),
                "payload": {
                    "conversationEvent": {
                        "role": "USER",
                        "content": [{"text": f"[{turn['speaker']}]: {turn['text']}"}],
                    }
                },
            }
        )
    return events


def ingest_transcript(agentcore_client, registry, transcript: dict) -> None:
    meta = transcript.get("metadata", {})
    meeting_id = meta.get("meeting_id", "unknown-meeting")
    project_key = meta.get("project_key", "unknown-project")
    meeting_type = meta.get("meeting_type", "")

    print(f"\nIngesting: meeting='{meeting_id}'  project='{project_key}'  type='{meeting_type}'")

    memory_id = resolve_memory_id(agentcore_client, registry, project_key)

    turns = parse_transcript(transcript)
    if not turns:
        print("  No turns found — nothing to ingest.")
        return

    events = build_conversation_events(turns)

    print(f"  Sending {len(events)} events → memory {memory_id}...")
    response = agentcore_client.ingest_conversation_events(
        memoryId=memory_id,
        conversationId=meeting_id,   # each meeting is its own conversation thread
        conversationEvents=events,
    )

    ingestion_job = response.get("ingestionJob", {})
    print(f"  Job submitted — id: {ingestion_job.get('ingestionJobId', 'n/a')}, "
          f"status: {ingestion_job.get('status', 'n/a')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Ingest stand-up transcripts into per-project AWS AgentCore Memory resources."
    )
    parser.add_argument("--transcript", help="Path to the transcript JSON file.")
    parser.add_argument("--list-projects", action="store_true", help="Print all registered project→memory mappings.")
    parser.add_argument("--region", default=AWS_REGION)
    args = parser.parse_args()

    agentcore_client = get_agentcore_client(region=args.region)
    registry = get_registry(region=args.region)

    if args.list_projects:
        response = registry.scan()
        items = response.get("Items", [])
        if not items:
            print("No projects registered yet.")
        for item in sorted(items, key=lambda x: x["project_key"]):
            print(f"  {item['project_key']:<30} → {item['memory_id']}  (created {item['created_at']})")
        return

    if not args.transcript:
        parser.error("--transcript is required.")

    with open(args.transcript, "r", encoding="utf-8") as f:
        transcript = json.load(f)

    try:
        ingest_transcript(agentcore_client, registry, transcript)
    except ClientError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

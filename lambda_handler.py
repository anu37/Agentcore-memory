"""
AWS Lambda handler for automatic per-project transcript ingestion.

Trigger: S3 ObjectCreated event on the transcripts bucket.
Required environment variables:
    MEMORY_REGISTRY_TABLE   — DynamoDB table name (e.g. "agentcore-memory-registry")
    AWS_REGION_NAME         — AWS region (default: us-east-1)

Lambda execution role needs:
    - s3:GetObject on the transcripts bucket
    - dynamodb:GetItem, PutItem on the registry table
    - bedrock-agentcore:CreateMemory
    - bedrock-agentcore:IngestConversationEvents
"""

import json
import os
import urllib.parse

import boto3

from ingest_transcript import get_agentcore_client, get_registry, ingest_transcript

REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")

s3_client = boto3.client("s3")
agentcore_client = get_agentcore_client(region=REGION)
registry = get_registry(region=REGION)


def handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        if not key.endswith(".json"):
            print(f"Skipping non-JSON file: {key}")
            continue

        print(f"Processing s3://{bucket}/{key}")

        response = s3_client.get_object(Bucket=bucket, Key=key)
        transcript = json.loads(response["Body"].read())

        ingest_transcript(agentcore_client, registry, transcript)

    return {"statusCode": 200}

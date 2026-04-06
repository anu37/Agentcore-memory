# AgentCore Memory — Transcript Ingestion & Retrieval

Ingest meeting transcripts into per-project **AWS AgentCore Memory** resources and retrieve semantic context from them. One AgentCore Memory resource is created per project and tracked in DynamoDB.

---

## Architecture

```
Meeting Transcript (JSON)
        │
        ▼
┌───────────────────┐       S3 ObjectCreated event
│  S3 Transcripts   │ ─────────────────────────────────┐
│     Bucket        │                                   │
└───────────────────┘                                   ▼
                                           ┌─────────────────────┐
                                           │   AWS Lambda        │
                                           │  (lambda_handler)   │
                                           └────────┬────────────┘
                                                    │
                              ┌─────────────────────┼──────────────────────┐
                              ▼                     ▼                      ▼
                 ┌────────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
                 │     DynamoDB       │  │  ingest_transcript│  │  AWS AgentCore      │
                 │  Memory Registry   │  │  (parse + build   │  │  Memory (per-project│
                 │ project_key →      │  │   events)         │  │  semantic store)    │
                 │  memory_id         │  └──────────────────┘  └─────────────────────┘
                 └────────────────────┘
```

### Components

| File | Purpose |
|---|---|
| `ingest_transcript.py` | Core logic: parse transcripts, manage project→memory registry, ingest events into AgentCore |
| `lambda_handler.py` | AWS Lambda entry point — triggered by S3 `ObjectCreated` events |
| `retrieve_memory.py` | CLI tool to semantically query a project's memory |

### Data Flow

1. A transcript JSON file is uploaded to the S3 transcripts bucket.
2. S3 triggers the Lambda function via an `ObjectCreated` event.
3. Lambda reads the transcript and calls `ingest_transcript()`.
4. For each project (identified by `metadata.project_key`), the registry is checked in DynamoDB.
   - If no memory exists for the project, a new AgentCore Memory resource is created and registered.
5. Transcript segments are parsed into speaker turns and sent to AgentCore as `conversationEvents`.
6. Each meeting is stored as its own conversation thread (`conversationId = meeting_id`).
7. Later, `retrieve_memory.py` performs semantic search over a project's memory using a natural-language query.

### Transcript JSON Format

```json
{
  "metadata": {
    "meeting_id": "standup-2026-04-06",
    "project_key": "my-project",
    "meeting_type": "standup",
    "started_at": "2026-04-06T09:00:00Z"
  },
  "segments": [
    {
      "speaker": "Alice",
      "segments": [
        { "text": "Finished the auth module.", "start": 0, "end": 5 }
      ]
    }
  ]
}
```

---

## Local Setup

### Prerequisites

- Python 3.10+
- AWS credentials configured (`aws configure` or environment variables)
- Access to AWS services: S3, DynamoDB, Bedrock AgentCore

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MEMORY_REGISTRY_TABLE` | `agentcore-memory-registry` | DynamoDB table name for project→memory mappings |
| `AWS_REGION_NAME` | `us-east-1` | AWS region |

Set them in your shell or a `.env` file:

```bash
export MEMORY_REGISTRY_TABLE=agentcore-memory-registry
export AWS_REGION_NAME=us-east-1
```

### DynamoDB Table Setup

Create the registry table (one-time setup):

```bash
aws dynamodb create-table \
  --table-name agentcore-memory-registry \
  --attribute-definitions AttributeName=project_key,AttributeType=S \
  --key-schema AttributeName=project_key,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

### IAM Permissions Required

The AWS principal (user or Lambda role) needs:

```json
{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:Scan",
    "bedrock-agentcore:CreateMemory",
    "bedrock-agentcore:IngestConversationEvents",
    "bedrock-agentcore:RetrieveMemories"
  ],
  "Resource": "*"
}
```

---

## Usage

### Ingest a Transcript (CLI)

```bash
python ingest_transcript.py --transcript path/to/transcript.json
```

### List All Registered Projects

```bash
python ingest_transcript.py --list-projects
```

### Retrieve Context from Memory

```bash
python retrieve_memory.py --project "my-project" --query "action items from last standup"
python retrieve_memory.py --project "my-project" --query "observability budget decision" --top-k 10
```

### Lambda Deployment

Package and deploy to AWS Lambda with:
- **Runtime**: Python 3.12
- **Handler**: `lambda_handler.handler`
- **Trigger**: S3 `ObjectCreated` event on your transcripts bucket
- **Environment variables**: `MEMORY_REGISTRY_TABLE`, `AWS_REGION_NAME`

```bash
pip install -r requirements.txt -t package/
cp *.py package/
cd package && zip -r ../function.zip . && cd ..
aws lambda update-function-code --function-name <your-function> --zip-file fileb://function.zip
```

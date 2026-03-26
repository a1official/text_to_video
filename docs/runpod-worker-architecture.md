# Runpod Worker Architecture

## Goal

Use Runpod for all GPU-heavy work while keeping the current control plane unchanged:

- local FastAPI app
- AWS S3 for assets
- AWS DynamoDB for metadata and jobs
- AWS Bedrock for planning
- Runpod for `Wan`, `HuMo`, and optional `LTX`

## Recommended First Setup

Start with:

- `1 x Runpod Pod`
- install `Wan2.2` first
- attach persistent storage or volume
- run a long-lived worker process that polls DynamoDB

After that:

- add `HuMo` on the same Pod if capacity allows, or on a second Pod

## Worker Responsibilities

Each Runpod worker should:

1. poll DynamoDB for jobs matching its worker type
2. download references from S3
3. run the model locally on the Pod
4. upload outputs to S3
5. mark the job complete in DynamoDB

## Suggested Pod Layout

```text
/workspace/text2video/
  app/
  runtime/
  logs/

/workspace/models/
  wan/
  humo/
  ltx/

/workspace/cache/
  huggingface/
  torch/
```

## Required Environment Variables

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN` if needed
- `AWS_DEFAULT_REGION`
- `S3_BUCKET`
- `RUNPOD_API_KEY`
- `RUNPOD_POD_ID`

## Immediate Next Step

When you're ready for compute bring-up, the next task is:

1. create the first Runpod Pod
2. install `Wan2.2`
3. copy the worker code onto the Pod
4. connect it to the existing DynamoDB queue

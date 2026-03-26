# Text 2 Video

Seedance-like video generation platform built with an AWS-lite deployment model:

- `S3` for assets and generated outputs
- `DynamoDB` for metadata and job state
- `Bedrock` for planning and orchestration intelligence
- `Runpod` for GPU workers and model runtimes

## Structure

```text
apps/
  api/
  worker/
packages/
  python/
scripts/
docs/
```

## Quick start

1. Fill in [`.env`](D:\openCLI\text 2 video\.env).
   Bedrock can use `BEDROCK_API_KEY`, while `S3` and `DynamoDB` still use normal AWS credentials.
2. Create a virtual environment.
3. Install dependencies from `pyproject.toml`.
4. Run `python scripts/bootstrap_aws.py` to create the base AWS resources.
5. Start the API.
6. Start the worker.

## Current status

This scaffold includes:

- FastAPI API service
- worker entry point
- Bedrock planner client with API-key support
- S3 helper
- DynamoDB-backed job queue
- bootstrap script for bucket and tables
- local control-plane-only execution guardrails
- a lightweight web dashboard at `/`
- real local FFmpeg stitch execution for stitch jobs when segment files exist

## Control-plane only

This repository currently does **not** run video generation models locally.

The local worker is limited to orchestration-safe tasks such as:

- planning
- queue management
- metadata updates
- stitch job execution with FFmpeg if the expected local segment files exist

Model execution jobs are intentionally blocked until you explicitly decide to attach a real Runpod GPU worker later.

Model-specific adapters are the next layer to add.

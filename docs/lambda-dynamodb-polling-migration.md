# Lambda + DynamoDB Polling Migration

This repo now has the first serverless control-plane slice for the video pipeline.

## What runs in Lambda

- Create a project
- Store and plan a shot list
- Queue render jobs from the stored plan
- Create stitch manifests
- Poll project state in DynamoDB and advance the workflow

## What stays on RunPod

- Keyframe generation
- Image-to-video motion generation
- TTS-heavy or model-heavy rendering
- Final clip rendering when GPU work is needed

## State storage

- `DynamoProjectStore` stores project metadata, plans, shots, stitch manifests, and outputs
- `DynamoJobQueue` stores job records and status transitions
- S3 remains the blob store for images, audio, clips, and final renders

## Lambda entrypoint

Use:

```python
from text2video.orchestrator.lambda_handler import lambda_handler
```

Supported actions:

- `create_project`
- `plan_project`
- `queue_jobs`
- `stitch_project`
- `poll_project`

## Polling flow

The poll step checks DynamoDB for job state:

1. If the project has a plan but no jobs, it queues render jobs.
2. If jobs are still active, it returns `active`.
3. If any job failed, it returns `failed`.
4. When renders are complete, it creates the stitch manifest and stitch job.
5. When the stitch job completes, it marks the project `complete`.

## Next deployment step

- Package `text2video.orchestrator.lambda_handler.lambda_handler` as the AWS Lambda handler.
- Point the function at the project DynamoDB tables and S3 bucket through environment variables.
- Trigger the Lambda from the API or a scheduled poller until the final output is ready.

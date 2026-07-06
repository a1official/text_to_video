# Lambda Orchestrator IAM

Use these policies for the Lambda control plane that polls DynamoDB and advances the workflow.

## Trust policy

Attach [infra/iam/lambda-orchestrator-trust-policy.json](/D:/openCLI/text%202%20video/infra/iam/lambda-orchestrator-trust-policy.json) to the role.

## Permissions policy

Attach [infra/iam/lambda-orchestrator-execution-policy.json](/D:/openCLI/text%202%20video/infra/iam/lambda-orchestrator-execution-policy.json) to the role.

## What it covers

- Read/write access to `t2v-projects`
- Read/write access to `t2v-jobs`
- Read/write access to `t2v-outputs`
- Read/write access to `t2v-continuity`
- CloudWatch Logs for Lambda execution

## Why this is enough

The Lambda control plane only needs to:

1. read the current project and job state
2. write workflow state updates
3. queue or advance jobs by updating DynamoDB
4. log its progress to CloudWatch

## If you want to verify with the CLI user

Temporarily attach [infra/iam/temp-dynamo-story-pipeline-access.json](/D:/openCLI/text%202%20video/infra/iam/temp-dynamo-story-pipeline-access.json) to the IAM identity you use for local CLI tests, then remove it once the Lambda role is validated.

Suggested inline policy name: `TempDynamoStoryPipelineAccess`

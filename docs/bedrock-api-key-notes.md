# Bedrock API Key Notes

This scaffold supports using an Amazon Bedrock API key for Bedrock model calls.

## What it is used for

- prompt planning
- shot decomposition
- prompt rewriting
- future Bedrock-hosted inference calls

## What it is not used for

The Bedrock API key does not replace your AWS credentials for:

- `S3`
- `DynamoDB`
- other AWS services outside Bedrock

## Current implementation

- Set `BEDROCK_API_KEY` in [`.env`](D:\openCLI\text 2 video\.env)
- The planner client maps it to `AWS_BEARER_TOKEN_BEDROCK`
- `boto3` then uses that bearer token for Bedrock Runtime calls

## References

- <https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html>
- <https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html>

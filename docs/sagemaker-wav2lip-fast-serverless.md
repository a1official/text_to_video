# SageMaker Serverless Wav2Lip-fast

This guide sets up a CPU-only `Wav2Lip-fast` deployment on SageMaker Serverless Inference.

## What this setup does

- builds a custom SageMaker-compatible container
- pushes the image to ECR
- creates a SageMaker model
- creates a serverless endpoint config
- creates or updates the endpoint
- stores generated videos in S3

## Important limits

SageMaker Serverless Inference does not use GPUs. It also has:

- up to `6 GB` memory per invocation
- a `60 second` max processing window
- a `5 GB` ephemeral storage limit

That means this setup is best for:

- short clips
- CPU-friendly jobs
- small face images or compact input videos

## Files added

- [Dockerfile](D:\openCLI\text 2 video\infra\sagemaker-serverless\wav2lip-fast\Dockerfile)
- [serve.py](D:\openCLI\text 2 video\infra\sagemaker-serverless\wav2lip-fast\serve.py)
- [deploy-wav2lip-fast.ps1](D:\openCLI\text 2 video\scripts\sagemaker\deploy-wav2lip-fast.ps1)

## Required IAM setup

You already created the execution role:

- `SageMakerWav2LipExecutionRole`

Your deploy-side identity also needs:

- `sagemaker:CreateModel`
- `sagemaker:CreateEndpointConfig`
- `sagemaker:CreateEndpoint`
- `sagemaker:UpdateEndpoint`
- `sagemaker:DescribeEndpoint`
- `iam:PassRole` for the execution role
- ECR permissions for build/push
- S3 permissions for input/output storage

## Environment variables

Add these to `.env` before running the deployment script:

```env
SAGEMAKER_WAV2LIP_EXECUTION_ROLE_ARN=arn:aws:iam::716314429284:role/SageMakerWav2LipExecutionRole
SAGEMAKER_WAV2LIP_OUTPUT_BUCKET=<your-s3-bucket>
SAGEMAKER_WAV2LIP_OUTPUT_PREFIX=wav2lip-fast/outputs
SAGEMAKER_WAV2LIP_ENDPOINT_NAME=wav2lip-fast-serverless
SAGEMAKER_WAV2LIP_MODEL_NAME=wav2lip-fast-serverless-model
SAGEMAKER_WAV2LIP_ENDPOINT_CONFIG_NAME=wav2lip-fast-serverless-config
SAGEMAKER_WAV2LIP_ECR_REPOSITORY=wav2lip-fast-serverless
SAGEMAKER_WAV2LIP_MEMORY_MB=6144
SAGEMAKER_WAV2LIP_MAX_CONCURRENCY=1

WAV2LIP_REPO_URL=https://github.com/ohsugi/Wav2Lip-fast.git
WAV2LIP_REPO_REF=main
WAV2LIP_CHECKPOINT_S3_URI=s3://<your-bucket>/models/wav2lip.pth
```

## Deploy

Run the deploy script from PowerShell:

```powershell
.\scripts\sagemaker\deploy-wav2lip-fast.ps1
```

The script will:

1. create or reuse the ECR repository
2. build the image from `infra/sagemaker-serverless/wav2lip-fast/Dockerfile`
3. push the image to ECR
4. create the SageMaker model
5. create the serverless endpoint config
6. create or update the endpoint

## Invocation shape

The container accepts JSON like this:

```json
{
  "face_image_url": "s3://your-bucket/uploads/face.png",
  "audio_url": "s3://your-bucket/uploads/audio.wav",
  "output_s3_key": "wav2lip-fast/outputs/result.mp4"
}
```

You can also pass `https://` URLs or local file paths while testing locally.

## Local smoke test

If you want to test the container before deployment, run it locally with Docker and send a POST request to `/invocations`.

## Recommendation

Use the full `6144 MB` memory setting first. If it runs comfortably, you can lower it later.

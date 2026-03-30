# Runpod Serverless LTX Deployment

This document describes how to deploy the working LTX commercial pipeline as a Runpod **Serverless load-balancing endpoint**.

## Why load-balancing mode

The current LTX service already exposes HTTP routes:

- `GET /health`
- `POST /ltx/generate-preview`
- `GET /jobs/{job_id}`

So the best fit is a **load-balancing endpoint** with a custom HTTP server, not a simple `handler(event)` worker.

## Validated target

- GPU class: `NVIDIA RTX 6000 Ada Generation`
- VRAM: `48 GB`
- Base image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- Exposed port: `8000`

## Files added for serverless

- [Dockerfile](D:\openCLI\text 2 video\infra\runpod-serverless\ltx\Dockerfile)
- [start-serverless-ltx.sh](D:\openCLI\text 2 video\infra\runpod-serverless\ltx\start-serverless-ltx.sh)

## Container behavior

At startup the container:

1. creates cache/temp/model directories under `/runpod-volume`
2. checks for:
   - `ltx-2.3-22b-distilled.safetensors`
   - `ltx-2.3-spatial-upscaler-x2-1.0.safetensors`
   - `google/gemma-3-12b-it-qat-q4_0-unquantized`
3. downloads missing assets if `HF_TOKEN` is provided
4. starts the FastAPI service on `PORT` (default `8000`)

## Build and push the image

Example with Docker Hub:

```powershell
docker build -f .\infra\runpod-serverless\ltx\Dockerfile -t <dockerhub-user>/text2video-ltx-serverless:latest .
docker push <dockerhub-user>/text2video-ltx-serverless:latest
```

## Create the Runpod Serverless endpoint

In Runpod Serverless:

1. create a **Load Balancing Endpoint**
2. choose a `48 GB` GPU class:
   - `RTX 6000 Ada Generation`
   - or equivalent `L40 / L40S / 6000 Ada PRO`
3. set the image to:

```text
<dockerhub-user>/text2video-ltx-serverless:latest
```

4. expose port:

```text
8000
```

5. mount a network volume and make it available at:

```text
/runpod-volume
```

## Required environment variables

Set these on the endpoint:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
AWS_DEFAULT_REGION=us-east-1
HF_TOKEN=<huggingface token with access to Gemma>
```

Optional overrides:

```text
PORT=8000
LTX_ASSETS_ROOT=/runpod-volume/models/ltx/LTX-2.3
LTX_GEMMA_ROOT=/runpod-volume/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized
```

## First-run behavior

The first serverless worker cold start may take a long time because it may:

- download gated Gemma assets
- download the LTX checkpoint
- download the spatial upscaler
- warm the Python environment

After the assets are present on the network volume, subsequent workers should come up much faster.

## Local config

After deployment, set:

```env
RUNPOD_LTX_INFERENCE_BASE_URL=https://<your-serverless-endpoint-host>
```

Use the root endpoint host, not a terminal URL.

## Health check

```bash
curl https://<your-serverless-endpoint-host>/health
```

Expected response:

```json
{"status":"ok","sdxl_loaded":true,"wan_repo_present":false}
```

## Local commercial run

```powershell
. .\scripts\load-aws-env.ps1
.\.venv\Scripts\python.exe .\scripts\run_ltx_commercial.py `
  --project-id hs-commercial-002 `
  --product-image-key uploads/product-test/HeadShoulders_SH_DeepScalpCleanse_front.png
```

## Cost note

Runpod Serverless pricing changes over time. For current pricing, see:

- [Runpod Serverless pricing](https://docs.runpod.io/serverless/pricing)

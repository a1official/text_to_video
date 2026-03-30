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

## Runpod Rebuild Notes

Validated commercial-generation pod:

- GPU: `NVIDIA RTX 6000 Ada Generation`
- VRAM: `48 GB`
- Recommended image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`

Validated LTX asset links used by the commercial pipeline:

- LTX checkpoint:
  [Lightricks/LTX-2.3 - ltx-2.3-22b-distilled.safetensors](https://huggingface.co/Lightricks/LTX-2.3/blob/main/ltx-2.3-22b-distilled.safetensors)
- LTX spatial upscaler:
  [Lightricks/LTX-2.3 - ltx-2.3-spatial-upscaler-x2-1.0.safetensors](https://huggingface.co/Lightricks/LTX-2.3/blob/main/ltx-2.3-spatial-upscaler-x2-1.0.safetensors)
- Gemma text encoder assets:
  [google/gemma-3-12b-it-qat-q4_0-unquantized](https://huggingface.co/google/gemma-3-12b-it-qat-q4_0-unquantized)

Notes:

- The Gemma repo is gated, so Hugging Face login is required on the pod before download.
- The bootstrap script [bootstrap-ltx-service.sh](D:\openCLI\text 2 video\scripts\runpod\bootstrap-ltx-service.sh) already includes the temp/cache redirection needed to avoid root-disk extraction failures on Runpod.
- The commercial runner [run_ltx_commercial.py](D:\openCLI\text 2 video\scripts\run_ltx_commercial.py) is the reproducible entrypoint for:
  - Bedrock script generation
  - LTX shot generation
  - FFmpeg stitch
  - Polly voiceover mux

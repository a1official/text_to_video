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
- Serverless deployment guide:
  [runpod-ltx-serverless.md](D:\openCLI\text 2 video\docs\runpod-ltx-serverless.md)

## Exact Rebuild Steps

Use these exact steps to rebuild the validated commercial pipeline on a fresh Runpod pod.

### 1. Create the Runpod pod

From Windows PowerShell with `runpodctl` available:

```powershell
$env:RUNPOD_API_KEY="<YOUR_RUNPOD_API_KEY>"
tools\bin\runpodctl.exe pod create `
  --name text2video-ltx-commercial `
  --image runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04 `
  --gpu-id "NVIDIA RTX 6000 Ada Generation" `
  --volume-in-gb 200 `
  --container-disk-in-gb 40 `
  --ports "8888/http"
```

Validated pod target:

- GPU: `NVIDIA RTX 6000 Ada Generation`
- VRAM: `48 GB`
- Volume: `200 GB`
- Container disk: `40 GB`

### 2. Clone this branch on the pod

On the pod:

```bash
mkdir -p /workspace/text2video
cd /workspace/text2video
git clone https://github.com/a1official/text_to_video.git app
cd /workspace/text2video/app
git checkout codex/ltx-commercial-pipeline
git pull
```

### 3. Bootstrap the dedicated LTX service

On the pod:

```bash
cd /workspace/text2video/app
chmod +x ./scripts/runpod/bootstrap-ltx-service.sh
chmod +x ./scripts/runpod/start-ltx-service.sh
./scripts/runpod/bootstrap-ltx-service.sh
```

### 4. Authenticate Hugging Face for Gemma

The Gemma repo is gated and requires a Hugging Face token with read access.

On the pod:

```bash
source /workspace/text2video/venv/bin/activate
huggingface-cli login
```

Then paste the token when prompted.

If Gemma did not fully download during bootstrap, rerun only the gated model download:

```bash
source /workspace/text2video/venv/bin/activate
python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="google/gemma-3-12b-it-qat-q4_0-unquantized",
    local_dir="/workspace/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized",
)
PY
```

### 5. Start the LTX backend

On the pod:

```bash
cd /workspace/text2video/app
source /workspace/text2video/venv/bin/activate
SERVICE_PORT=8888 ./scripts/runpod/start-ltx-service.sh
```

Health check:

```bash
curl http://127.0.0.1:8888/health
```

Expected public API form:

```text
https://<POD_ID>-8888.proxy.runpod.net
```

### 6. Point local config at the LTX pod

In [`.env`](D:\openCLI\text 2 video\.env):

```env
RUNPOD_LTX_INFERENCE_BASE_URL=https://<POD_ID>-8888.proxy.runpod.net
```

### 7. Load AWS credentials locally

From Windows PowerShell in the repo:

```powershell
. .\scripts\load-aws-env.ps1
```

Verify:

```powershell
aws sts get-caller-identity
```

### 8. Upload the product image to S3

Example upload:

```powershell
. .\scripts\load-aws-env.ps1
aws s3 cp "$HOME\Downloads\HeadShoulders_SH_DeepScalpCleanse_front.png" "s3://t2v-assets-716314429284-us-east-1/uploads/product-test/HeadShoulders_SH_DeepScalpCleanse_front.png"
```

Example resulting S3 key:

```text
uploads/product-test/HeadShoulders_SH_DeepScalpCleanse_front.png
```

### 9. Run the full commercial pipeline locally

This uses:

- Bedrock for script + shot planning
- LTX for per-shot generation
- FFmpeg for stitch
- Polly for final deterministic voiceover

Command:

```powershell
. .\scripts\load-aws-env.ps1
.\.venv\Scripts\python.exe .\scripts\run_ltx_commercial.py `
  --project-id hs-commercial-002 `
  --product-image-key uploads/product-test/HeadShoulders_SH_DeepScalpCleanse_front.png
```

Optional voice override:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_ltx_commercial.py `
  --project-id hs-commercial-002 `
  --product-image-key uploads/product-test/HeadShoulders_SH_DeepScalpCleanse_front.png `
  --voice-id Joanna
```

### 10. Outputs you should expect

The script prints JSON containing:

- `concept`
- `voiceover_script`
- `supers`
- `shots`
- `segments`
- `stitched_output_uri`
- `voiced_output_uri`
- `final_output_uri`

Example output locations:

```text
s3://t2v-assets-716314429284-us-east-1/stitched/<project-id>/commercial.mp4
s3://t2v-assets-716314429284-us-east-1/stitched/<project-id>/commercial-vo.mp4
```

### 11. Important operational notes

- Do not commit downloaded model files, caches, or pod-local virtual environments to Git.
- If Hugging Face downloads fail due to disk space, keep using `/workspace/tmp`, `/workspace/pip-cache`, `/workspace/uv-cache`, and `/workspace/.cache` as configured in the bootstrap script.
- If the LTX terminal URL works but the API does not, make sure you are using the `8888` proxy URL, not the terminal proxy URL.
- If AWS uploads fail locally, always reload credentials first:

```powershell
. .\scripts\load-aws-env.ps1
```

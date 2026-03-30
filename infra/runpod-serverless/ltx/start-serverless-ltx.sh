#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  /runpod-volume/tmp \
  /runpod-volume/pip-cache \
  /runpod-volume/uv-cache \
  /runpod-volume/.cache \
  /runpod-volume/cache/huggingface \
  /runpod-volume/cache/torch \
  /runpod-volume/models/ltx/LTX-2.3

python3 - <<'PY'
import os
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""

assets_root = Path(os.environ["LTX_ASSETS_ROOT"])
gemma_root = Path(os.environ["LTX_GEMMA_ROOT"])
checkpoint_path = Path(os.environ["LTX_CHECKPOINT_PATH"])
spatial_upsampler_path = Path(os.environ["LTX_SPATIAL_UPSAMPLER_PATH"])

assets_root.mkdir(parents=True, exist_ok=True)
gemma_root.parent.mkdir(parents=True, exist_ok=True)

download_kwargs = {}
if hf_token:
    download_kwargs["token"] = hf_token

if not checkpoint_path.exists():
    hf_hub_download(
        repo_id="Lightricks/LTX-2.3",
        filename=checkpoint_path.name,
        local_dir=str(assets_root),
        **download_kwargs,
    )

if not spatial_upsampler_path.exists():
    hf_hub_download(
        repo_id="Lightricks/LTX-2.3",
        filename=spatial_upsampler_path.name,
        local_dir=str(assets_root),
        **download_kwargs,
    )

if not gemma_root.exists() or not any(gemma_root.iterdir()):
    if not hf_token:
        raise SystemExit(
            "Gemma assets are missing. Set HF_TOKEN or pre-populate "
            f"{gemma_root} on the network volume before starting the serverless worker."
        )
    snapshot_download(
        repo_id="google/gemma-3-12b-it-qat-q4_0-unquantized",
        local_dir=str(gemma_root),
        **download_kwargs,
    )
PY

cd /app
exec python3 -m uvicorn apps.runpod_ltx_service.main:app --host 0.0.0.0 --port "${PORT:-8000}"

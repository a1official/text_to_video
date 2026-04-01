#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

APP_ROOT="${APP_ROOT:-/workspace/text2video/app}"
APP_VENV="${APP_VENV:-/workspace/text2video/venv}"
LTX_REPO_ROOT="${LTX_REPO_ROOT:-/workspace/text2video/ltx2-official}"
LTX_ASSETS_ROOT="${LTX_ASSETS_ROOT:-/workspace/models/ltx/LTX-2.3}"
LTX_GEMMA_ROOT="${LTX_GEMMA_ROOT:-/workspace/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized}"
LTX_CHECKPOINT_PATH="${LTX_CHECKPOINT_PATH:-${LTX_ASSETS_ROOT}/ltx-2.3-22b-distilled.safetensors}"
LTX_SPATIAL_UPSAMPLER_PATH="${LTX_SPATIAL_UPSAMPLER_PATH:-${LTX_ASSETS_ROOT}/ltx-2.3-spatial-upscaler-x2-1.0.safetensors}"

apt-get update
apt-get install -y \
  ffmpeg \
  git \
  git-lfs \
  libgl1 \
  libglib2.0-0 \
  python3-pip \
  python3-venv

git lfs install --system

mkdir -p \
  /workspace/text2video \
  /workspace/text2video/logs \
  /workspace/tmp \
  /workspace/pip-cache \
  /workspace/uv-cache \
  /workspace/.cache \
  /workspace/cache/huggingface \
  /workspace/cache/torch \
  /workspace/models/ltx \
  "${LTX_ASSETS_ROOT}"

cat >/etc/profile.d/text2video-ltx.sh <<EOF
export TMPDIR=/workspace/tmp
export TEMP=/workspace/tmp
export TMP=/workspace/tmp
export PIP_CACHE_DIR=/workspace/pip-cache
export UV_CACHE_DIR=/workspace/uv-cache
export XDG_CACHE_HOME=/workspace/.cache
export HF_HOME=/workspace/cache/huggingface
export HUGGINGFACE_HUB_CACHE=/workspace/cache/huggingface/hub
export TORCH_HOME=/workspace/cache/torch
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export LTX_REPO_ROOT=${LTX_REPO_ROOT}
export LTX_PYTHON_BIN=${LTX_REPO_ROOT}/.venv/bin/python
export LTX_ASSETS_ROOT=${LTX_ASSETS_ROOT}
export LTX_GEMMA_ROOT=${LTX_GEMMA_ROOT}
export LTX_CHECKPOINT_PATH=${LTX_CHECKPOINT_PATH}
export LTX_SPATIAL_UPSAMPLER_PATH=${LTX_SPATIAL_UPSAMPLER_PATH}
EOF

export TMPDIR=/workspace/tmp
export TEMP=/workspace/tmp
export TMP=/workspace/tmp
export PIP_CACHE_DIR=/workspace/pip-cache
export UV_CACHE_DIR=/workspace/uv-cache
export XDG_CACHE_HOME=/workspace/.cache

python3 -m venv "${APP_VENV}"
source "${APP_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "${APP_ROOT}" "huggingface_hub==0.36.0" uv

if [ -d "${LTX_REPO_ROOT}/.git" ]; then
  git -C "${LTX_REPO_ROOT}" fetch --depth 1 origin main
  git -C "${LTX_REPO_ROOT}" reset --hard origin/main
else
  git clone --depth 1 https://github.com/Lightricks/LTX-2 "${LTX_REPO_ROOT}"
fi

cd "${LTX_REPO_ROOT}"
uv sync --frozen --no-dev

python - <<'PY'
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

assets_root = Path("/workspace/models/ltx/LTX-2.3")
assets_root.mkdir(parents=True, exist_ok=True)

hf_hub_download(
    repo_id="Lightricks/LTX-2.3",
    filename="ltx-2.3-22b-distilled.safetensors",
    local_dir=str(assets_root),
)
hf_hub_download(
    repo_id="Lightricks/LTX-2.3",
    filename="ltx-2.3-spatial-upscaler-x2-1.0.safetensors",
    local_dir=str(assets_root),
)

snapshot_download(
    repo_id="google/gemma-3-12b-it-qat-q4_0-unquantized",
    local_dir="/workspace/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized",
)
PY

echo "Runpod LTX service bootstrap completed."

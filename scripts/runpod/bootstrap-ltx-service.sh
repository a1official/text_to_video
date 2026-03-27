#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

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
  /workspace/text2video/app \
  /workspace/text2video/logs \
  /workspace/cache/huggingface \
  /workspace/cache/torch \
  /workspace/models/ltx

cat >/etc/profile.d/text2video-ltx.sh <<'EOF'
export HF_HOME=/workspace/cache/huggingface
export HUGGINGFACE_HUB_CACHE=/workspace/cache/huggingface/hub
export TORCH_HOME=/workspace/cache/torch
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EOF

python3 -m venv /workspace/text2video/venv
source /workspace/text2video/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --index-url https://download.pytorch.org/whl/cu124 \
  torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0
python -m pip install -e /workspace/text2video/app
python -m pip install \
  "huggingface_hub==0.36.0" \
  "transformers==4.51.3" \
  "accelerate>=1.1.1" \
  sentencepiece \
  pillow \
  imageio \
  imageio-ffmpeg \
  "diffusers @ git+https://github.com/huggingface/diffusers"

python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="Lightricks/LTX-2",
    local_dir="/workspace/models/ltx/LTX-2",
    allow_patterns=[
        "model_index.json",
        "audio_vae/*",
        "connectors/*",
        "scheduler/*",
        "text_encoder/*",
        "tokenizer/*",
        "transformer/*",
        "vae/*",
        "vocoder/*",
    ],
)
PY

echo "Runpod LTX service bootstrap completed."

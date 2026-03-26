#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  build-essential \
  ffmpeg \
  git \
  git-lfs \
  libgl1 \
  libglib2.0-0 \
  pkg-config \
  python3-pip \
  python3-venv

git lfs install --system

mkdir -p \
  /workspace/text2video/app \
  /workspace/text2video/logs \
  /workspace/text2video/runtime \
  /workspace/cache/huggingface \
  /workspace/cache/torch \
  /workspace/models/wan

cat >/etc/profile.d/text2video-runpod.sh <<'EOF'
export HF_HOME=/workspace/cache/huggingface
export HUGGINGFACE_HUB_CACHE=/workspace/cache/huggingface/hub
export TORCH_HOME=/workspace/cache/torch
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EOF

if [ ! -d /workspace/models/wan/Wan2.2/.git ]; then
  git clone https://github.com/Wan-Video/Wan2.2.git /workspace/models/wan/Wan2.2
else
  git -C /workspace/models/wan/Wan2.2 pull --ff-only
fi

python3 -m venv /workspace/text2video/venv
source /workspace/text2video/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel

echo "Bootstrap completed."
echo "Next:"
echo "  1. copy the app repo into /workspace/text2video/app"
echo "  2. place the worker .env at /workspace/text2video/app/.env"
echo "  3. install model-specific dependencies in /workspace/models/wan/Wan2.2"
echo "  4. start scripts/runpod/start-wan-worker.sh"

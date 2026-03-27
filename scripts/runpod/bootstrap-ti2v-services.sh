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
  python3-venv \
  unzip

git lfs install --system

mkdir -p \
  /workspace/text2video/app \
  /workspace/text2video/logs \
  /workspace/text2video/runtime \
  /workspace/cache/huggingface \
  /workspace/cache/torch \
  /workspace/models/sdxl \
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
python -m pip install --index-url https://download.pytorch.org/whl/cu124 \
  torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0
python -m pip install -e /workspace/text2video/app
python -m pip install \
  "huggingface_hub[cli]" \
  "transformers==4.51.3" \
  "accelerate>=1.1.1" \
  "diffusers==0.36.0" \
  "safetensors>=0.4.3" \
  einops \
  decord \
  librosa \
  soundfile \
  sentencepiece
grep -v '^flash_attn' /workspace/models/wan/Wan2.2/requirements.txt >/tmp/wan-requirements-no-flash.txt
python -m pip install -r /tmp/wan-requirements-no-flash.txt

huggingface-cli download stabilityai/stable-diffusion-xl-base-1.0 --local-dir /workspace/models/sdxl/stable-diffusion-xl-base-1.0
huggingface-cli download Wan-AI/Wan2.2-TI2V-5B --local-dir /workspace/models/wan/Wan2.2-TI2V-5B

echo "Runpod TI2V service bootstrap completed."

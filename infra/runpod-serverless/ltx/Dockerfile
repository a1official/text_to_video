FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV APP_ROOT=/app
ENV LTX_REPO_ROOT=/opt/ltx2-official
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    git-lfs \
    libgl1 \
    libglib2.0-0 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN git lfs install --system

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY packages /app/packages
COPY apps /app/apps
COPY scripts /app/scripts

RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install -e /app "huggingface_hub==0.36.0" uv

RUN git clone --depth 1 https://github.com/Lightricks/LTX-2 /opt/ltx2-official
WORKDIR /opt/ltx2-official
RUN UV_CACHE_DIR=/tmp/uv-cache XDG_CACHE_HOME=/tmp/.cache uv sync --frozen --no-dev

WORKDIR /app
COPY infra/runpod-serverless/ltx/start-serverless-ltx.sh /start-serverless-ltx.sh
RUN chmod +x /start-serverless-ltx.sh

ENV HF_HOME=/runpod-volume/cache/huggingface
ENV HUGGINGFACE_HUB_CACHE=/runpod-volume/cache/huggingface/hub
ENV TORCH_HOME=/runpod-volume/cache/torch
ENV TMPDIR=/runpod-volume/tmp
ENV TEMP=/runpod-volume/tmp
ENV TMP=/runpod-volume/tmp
ENV PIP_CACHE_DIR=/runpod-volume/pip-cache
ENV UV_CACHE_DIR=/runpod-volume/uv-cache
ENV XDG_CACHE_HOME=/runpod-volume/.cache
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ENV LTX_PYTHON_BIN=/opt/ltx2-official/.venv/bin/python
ENV LTX_ASSETS_ROOT=/runpod-volume/models/ltx/LTX-2.3
ENV LTX_GEMMA_ROOT=/runpod-volume/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized
ENV LTX_CHECKPOINT_PATH=/runpod-volume/models/ltx/LTX-2.3/ltx-2.3-22b-distilled.safetensors
ENV LTX_SPATIAL_UPSAMPLER_PATH=/runpod-volume/models/ltx/LTX-2.3/ltx-2.3-spatial-upscaler-x2-1.0.safetensors
ENV PORT=8000

EXPOSE 8000
ENTRYPOINT ["/start-serverless-ltx.sh"]

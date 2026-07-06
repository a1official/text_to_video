FROM public.ecr.aws/docker/library/python:3.10-slim-bullseye

ARG WAV2LIP_REPO_URL=https://github.com/ohsugi/Wav2Lip-fast.git
ARG WAV2LIP_REPO_REF=master

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    WAV2LIP_REPO_ROOT=/opt/program/Wav2Lip-fast \
    MODEL_DIR=/opt/ml/model \
    TEMP_DIR=/tmp/wav2lip-fast

WORKDIR /opt/program

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    libglib2.0-0 \
    libgl1 \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${WAV2LIP_REPO_REF}" "${WAV2LIP_REPO_URL}" "${WAV2LIP_REPO_ROOT}"

COPY serve.py /opt/program/serve.py
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir \
        fastapi \
        uvicorn[standard] \
        boto3 \
        httpx \
        pydantic \
        python-dotenv \
        numpy \
        scipy \
        librosa \
        soundfile \
        opencv-python-headless \
        pillow \
        torch \
        torchaudio \
    && if [ -f "${WAV2LIP_REPO_ROOT}/requirements.txt" ]; then python -m pip install --no-cache-dir -r "${WAV2LIP_REPO_ROOT}/requirements.txt"; fi

RUN mkdir -p "${MODEL_DIR}" "${TEMP_DIR}"

EXPOSE 8080

CMD ["serve"]

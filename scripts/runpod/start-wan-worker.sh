#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/workspace/text2video/app}"
VENV_PATH="${VENV_PATH:-/workspace/text2video/venv}"

if [ ! -f "${APP_ROOT}/pyproject.toml" ]; then
  echo "App source not found at ${APP_ROOT}"
  echo "Copy this repo to the pod first."
  exit 1
fi

if [ -f /etc/profile.d/text2video-runpod.sh ]; then
  # shellcheck disable=SC1091
  source /etc/profile.d/text2video-runpod.sh
fi

if [ ! -d "${VENV_PATH}" ]; then
  python3 -m venv "${VENV_PATH}"
fi

source "${VENV_PATH}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "${APP_ROOT}"

export WORKER_ID="${WORKER_ID:-runpod-wan-worker}"
export WORKER_TYPE="${WORKER_TYPE:-wan}"

cd "${APP_ROOT}"
python apps/worker/main.py

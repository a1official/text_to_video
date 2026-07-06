#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/workspace/text2video/app}"
VENV_PATH="${VENV_PATH:-/workspace/text2video/venv}"
WAN_PORT="${WAN_PORT:-8000}"
LTX_PORT="${LTX_PORT:-8888}"

if [ ! -f "${APP_ROOT}/pyproject.toml" ]; then
  echo "App source not found at ${APP_ROOT}"
  echo "Copy this repo to the pod first."
  exit 1
fi

if [ -f /etc/profile.d/text2video-runpod.sh ]; then
  # shellcheck disable=SC1091
  source /etc/profile.d/text2video-runpod.sh
fi

source "${VENV_PATH}/bin/activate"
cd "${APP_ROOT}"

export RUNPOD_WAN_INFERENCE_BASE_URL="${RUNPOD_WAN_INFERENCE_BASE_URL:-http://127.0.0.1:${WAN_PORT}}"
export RUNPOD_LTX_INFERENCE_BASE_URL="${RUNPOD_LTX_INFERENCE_BASE_URL:-http://127.0.0.1:${LTX_PORT}}"

python -m uvicorn apps.runpod_service.main:app --host 0.0.0.0 --port "${WAN_PORT}" >/workspace/text2video/logs/runpod-wan-service.log 2>&1 &
WAN_SERVICE_PID=$!

python -m uvicorn apps.runpod_ltx_service.main:app --host 0.0.0.0 --port "${LTX_PORT}" >/workspace/text2video/logs/runpod-ltx-service.log 2>&1 &
LTX_SERVICE_PID=$!

cleanup() {
  kill "${WAN_SERVICE_PID}" "${LTX_SERVICE_PID}" "${WAN_WORKER_PID:-}" "${GENERAL_WORKER_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

export WORKER_ID="${WAN_WORKER_ID:-runpod-wan-worker}"
export WORKER_TYPE="wan"
python apps/worker/main.py >/workspace/text2video/logs/runpod-wan-worker.log 2>&1 &
WAN_WORKER_PID=$!

export WORKER_ID="${GENERAL_WORKER_ID:-runpod-general-worker}"
export WORKER_TYPE="general"
python apps/worker/main.py >/workspace/text2video/logs/runpod-general-worker.log 2>&1 &
GENERAL_WORKER_PID=$!

wait "${WAN_SERVICE_PID}" "${LTX_SERVICE_PID}" "${WAN_WORKER_PID}" "${GENERAL_WORKER_PID}"

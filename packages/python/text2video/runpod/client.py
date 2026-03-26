from __future__ import annotations

import time

import httpx

from text2video.config import Settings
from text2video.runpod.schemas import (
    InferenceJobAccepted,
    InferenceJobStatus,
    SdxlGenerateRequest,
    SdxlGenerateResponse,
    WanGenerateRequest,
    WanGenerateResponse,
)


class RunpodInferenceClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.runpod_inference_base_url:
            raise ValueError("RUNPOD_INFERENCE_BASE_URL is not configured")
        self.base_url = settings.runpod_inference_base_url.rstrip("/")
        self.timeout = settings.runpod_request_timeout_sec

    def generate_sdxl_keyframe(self, request: SdxlGenerateRequest) -> SdxlGenerateResponse:
        response = httpx.post(
            f"{self.base_url}/sdxl/generate-keyframe",
            json=request.model_dump(),
            timeout=30,
        )
        response.raise_for_status()
        accepted = InferenceJobAccepted.model_validate(response.json())
        return SdxlGenerateResponse.model_validate(self._wait_for_job(accepted.job_id))

    def generate_wan_ti2v(self, request: WanGenerateRequest) -> WanGenerateResponse:
        response = httpx.post(
            f"{self.base_url}/wan/generate-ti2v",
            json=request.model_dump(),
            timeout=30,
        )
        response.raise_for_status()
        accepted = InferenceJobAccepted.model_validate(response.json())
        return WanGenerateResponse.model_validate(self._wait_for_job(accepted.job_id))

    def _wait_for_job(self, job_id: str) -> dict:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                response = httpx.get(
                    f"{self.base_url}/jobs/{job_id}",
                    timeout=30,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {502, 503, 504}:
                    time.sleep(10)
                    continue
                raise
            except httpx.TransportError:
                time.sleep(10)
                continue
            status = InferenceJobStatus.model_validate(response.json())
            if status.status == "completed":
                return status.result or {}
            if status.status == "failed":
                raise RuntimeError(status.error or f"Runpod job {job_id} failed")
            time.sleep(5)
        raise TimeoutError(f"Runpod job {job_id} did not finish within {self.timeout} seconds")

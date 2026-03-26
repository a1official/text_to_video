from __future__ import annotations

import httpx

from text2video.config import Settings
from text2video.runpod.schemas import (
    QwenGenerateRequest,
    QwenGenerateResponse,
    WanGenerateRequest,
    WanGenerateResponse,
)


class RunpodInferenceClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.runpod_inference_base_url:
            raise ValueError("RUNPOD_INFERENCE_BASE_URL is not configured")
        self.base_url = settings.runpod_inference_base_url.rstrip("/")
        self.timeout = settings.runpod_request_timeout_sec

    def generate_qwen_keyframe(self, request: QwenGenerateRequest) -> QwenGenerateResponse:
        response = httpx.post(
            f"{self.base_url}/qwen/generate-keyframe",
            json=request.model_dump(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return QwenGenerateResponse.model_validate(response.json())

    def generate_wan_ti2v(self, request: WanGenerateRequest) -> WanGenerateResponse:
        response = httpx.post(
            f"{self.base_url}/wan/generate-ti2v",
            json=request.model_dump(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return WanGenerateResponse.model_validate(response.json())

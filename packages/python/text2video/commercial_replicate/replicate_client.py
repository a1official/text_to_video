from __future__ import annotations

import time

import httpx

from text2video.config import Settings


class ReplicateClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.replicate_api_key:
            raise ValueError("REPLICATE_API_KEY is not configured")
        self.settings = settings
        self.base_url = settings.replicate_api_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Token {settings.replicate_api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }

    def generate_luma_video(
        self,
        *,
        prompt: str,
        start_image_url: str,
        duration_seconds: int = 5,
        aspect_ratio: str = "16:9",
    ) -> dict:
        response = httpx.post(
            f"{self.base_url}/models/{self.settings.replicate_video_model}/predictions",
            headers=self.headers,
            json={
                "input": {
                    "prompt": prompt,
                    "start_image_url": start_image_url,
                    "duration": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "loop": False,
                }
            },
            timeout=300,
        )
        if response.status_code == 402:
            raise RuntimeError("Replicate video generation requires billing or credits for the selected Luma model.")
        response.raise_for_status()
        payload = response.json()
        return self._resolve_prediction(payload)

    def _resolve_prediction(self, payload: dict) -> dict:
        status = str(payload.get("status") or "").lower()
        if status == "succeeded":
            return payload
        if status in {"failed", "canceled"}:
            raise RuntimeError(f"Replicate prediction failed: {payload}")
        get_url = (payload.get("urls") or {}).get("get")
        if not get_url:
            raise RuntimeError(f"Replicate prediction missing polling URL: {payload}")
        deadline = time.time() + 3600
        while time.time() < deadline:
            response = httpx.get(
                get_url,
                headers={"Authorization": f"Token {self.settings.replicate_api_key}"},
                timeout=120,
            )
            response.raise_for_status()
            polled = response.json()
            status = str(polled.get("status") or "").lower()
            if status == "succeeded":
                return polled
            if status in {"failed", "canceled"}:
                raise RuntimeError(f"Replicate prediction failed: {polled}")
            time.sleep(5)
        raise TimeoutError("Replicate video prediction did not finish in time")


def extract_replicate_output_url(payload: dict) -> str:
    output = payload.get("output")
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str) and item.startswith("http"):
                return item
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    raise KeyError(f"Could not find Replicate output URL in payload: {payload}")

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from text2video.config import Settings


class NvidiaCatalogClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is not configured")
        self.settings = settings
        self.base_url = settings.nvidia_api_base_url.rstrip("/")
        self.generate_path = settings.nvidia_video_generate_path
        self.headers = {
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Content-Type": "application/json",
        }

    def generate_video(
        self,
        *,
        image_path: Path,
        seed: int | None = None,
        cfg_scale: float = 1.8,
        motion_bucket_id: int = 127,
    ) -> dict:
        data_url = encode_image_as_data_url(image_path)
        response = httpx.post(
            f"{self.base_url}{self.generate_path}",
            headers=self.headers,
            json={
                "image": data_url,
                "seed": seed,
                "cfg_scale": cfg_scale,
                "motion_bucket_id": motion_bucket_id,
            },
            timeout=300,
        )
        if response.status_code >= 400:
            detail = response.text
            if response.status_code == 404 and "Not found for account" in detail:
                raise RuntimeError(
                    "NVIDIA_API_KEY is valid, but this account is not entitled for the configured NVIDIA video model."
                )
            raise RuntimeError(f"NVIDIA video generation request failed ({response.status_code}): {detail[:1200]}")
        payload = response.json()
        finish_reason = str(payload.get("finish_reason") or "").upper()
        if finish_reason and finish_reason != "SUCCESS":
            raise RuntimeError(f"NVIDIA video generation failed: {payload}")
        return payload


def encode_image_as_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def write_nvidia_video_payload(payload: dict, target_path: Path) -> Path:
    video_b64 = payload.get("video")
    if not isinstance(video_b64, str) or not video_b64:
        raise KeyError(f"Could not find NVIDIA base64 video in payload: {payload}")
    target_path.write_bytes(base64.b64decode(video_b64))
    return target_path

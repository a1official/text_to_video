from __future__ import annotations

import json
import time

import httpx

from text2video.config import Settings


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        self.settings = settings
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000/openrouter",
            "X-Title": "Mercury Studio Nextgen Commercials",
        }

    def chat_json(self, *, model: str, system_prompt: str, user_payload: dict) -> dict:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, indent=2)},
                ],
            },
            timeout=300,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)

    def generate_music(
        self,
        *,
        prompt: str,
        duration_seconds: int = 20,
    ) -> dict:
        response = httpx.post(
            f"{self.base_url}/audio/generations",
            headers=self.headers,
            json={
                "model": "google/lyria-3-pro-preview",
                "prompt": prompt,
                "duration": duration_seconds,
                "format": "mp3",
            },
            timeout=300,
        )
        response.raise_for_status()
        return response.json()

    def generate_video(
        self,
        *,
        model: str,
        prompt: str,
        image_url: str,
        duration_seconds: int,
        width: int,
        height: int,
    ) -> dict:
        response = httpx.post(
            f"{self.base_url}/videos/generations",
            headers=self.headers,
            json={
                "model": model,
                "prompt": prompt,
                "image_url": image_url,
                "duration": duration_seconds,
                "width": width,
                "height": height,
            },
            timeout=300,
        )
        response.raise_for_status()
        payload = response.json()
        return self._resolve_video_payload(payload)

    def _resolve_video_payload(self, payload: dict) -> dict:
        status = payload.get("status")
        if status in {None, "completed", "COMPLETED"}:
            return payload
        if status in {"queued", "processing", "IN_PROGRESS", "PENDING"}:
            job_id = payload.get("id") or payload.get("job_id")
            if not job_id:
                return payload
            return self._poll_video_job(job_id)
        return payload

    def _poll_video_job(self, job_id: str) -> dict:
        deadline = time.time() + self.settings.runpod_request_timeout_sec
        while time.time() < deadline:
            response = httpx.get(
                f"{self.base_url}/videos/generations/{job_id}",
                headers=self.headers,
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status") or "").lower()
            if status in {"completed", "succeeded", "success"}:
                return payload
            if status in {"failed", "cancelled", "timed_out"}:
                raise RuntimeError(f"OpenRouter video generation failed: {payload}")
            time.sleep(8)
        raise TimeoutError(f"Timed out waiting for OpenRouter video generation {job_id}")


def extract_video_url(payload: dict) -> str:
    candidates = [
        payload.get("video_url"),
        payload.get("url"),
        payload.get("result"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in ("url", "video_url", "result"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.startswith("http"):
                        return candidate

    output = payload.get("output")
    if isinstance(output, dict):
        for key in ("url", "video_url", "result"):
            candidate = output.get(key)
            if isinstance(candidate, str) and candidate.startswith("http"):
                return candidate

    raise KeyError(f"Could not find OpenRouter video URL in payload: {payload}")


def extract_audio_url(payload: dict) -> str:
    for key in ("audio_url", "url", "result"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in ("audio_url", "url", "result"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.startswith("http"):
                        return candidate

    raise KeyError(f"Could not find OpenRouter audio URL in payload: {payload}")

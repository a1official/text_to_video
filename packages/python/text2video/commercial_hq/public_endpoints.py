from __future__ import annotations

import time

import httpx

from text2video.config import Settings


class RunpodPublicEndpointClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.runpod_api_key:
            raise ValueError("RUNPOD_API_KEY is not configured")
        self.settings = settings
        self.api_key = settings.runpod_api_key
        self.timeout = settings.runpod_request_timeout_sec

    def _resolve_payload(self, payload: dict, base_url: str) -> dict:
        status = payload.get("status")
        if status == "COMPLETED":
            return payload
        if status in {"IN_PROGRESS", "IN_QUEUE", "QUEUED"}:
            job_id = payload.get("id")
            if not job_id:
                raise RuntimeError(f"Runpod public endpoint did not return a job id: {payload}")
            return self._poll_status(base_url=base_url, job_id=job_id)
        raise RuntimeError(f"Runpod public endpoint generation failed: {payload}")

    def _poll_status(self, *, base_url: str, job_id: str) -> dict:
        deadline = time.time() + self.timeout
        status_url = f"{base_url.rsplit('/', 1)[0]}/status/{job_id}"
        while time.time() < deadline:
            try:
                response = httpx.get(
                    status_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60,
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError:
                time.sleep(5)
                continue
            status = payload.get("status")
            if status == "COMPLETED":
                return payload
            if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise RuntimeError(f"Runpod public endpoint generation failed: {payload}")
            time.sleep(5)
        raise TimeoutError(f"Runpod public endpoint job {job_id} did not finish within {self.timeout} seconds")

    def generate_infinitetalk(
        self,
        *,
        prompt: str,
        image_url: str,
        audio_url: str,
        resolution: str = "480p",
        enable_safety_checker: bool = True,
    ) -> dict:
        response = httpx.post(
            self.settings.runpod_infinitetalk_base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": {
                    "prompt": prompt,
                    "image": image_url,
                    "audio": audio_url,
                    "resolution": resolution,
                    "enable_safety_checker": enable_safety_checker,
                }
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return self._resolve_payload(payload, self.settings.runpod_infinitetalk_base_url)

    def generate_seedance_i2v(
        self,
        *,
        prompt: str,
        image_url: str,
        duration: int = 5,
        resolution: str = "720p",
        aspect_ratio: str = "16:9",
        seed: int = -1,
    ) -> dict:
        response = httpx.post(
            self.settings.runpod_seedance_i2v_base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": {
                    "prompt": prompt,
                    "image": image_url,
                    "duration": duration,
                    "resolution": resolution,
                    "aspect_ratio": aspect_ratio,
                    "seed": seed,
                }
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return self._resolve_payload(payload, self.settings.runpod_seedance_i2v_base_url)

    def generate_nano_banana_2_edit(
        self,
        *,
        prompt: str,
        images: list[str],
        resolution: str = "1k",
        output_format: str = "png",
        enable_safety_checker: bool = True,
    ) -> dict:
        response = httpx.post(
            self.settings.runpod_nano_banana_2_edit_base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": {
                    "prompt": prompt,
                    "images": images,
                    "resolution": resolution,
                    "output_format": output_format,
                    "enable_safety_checker": enable_safety_checker,
                }
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return self._resolve_payload(payload, self.settings.runpod_nano_banana_2_edit_base_url)

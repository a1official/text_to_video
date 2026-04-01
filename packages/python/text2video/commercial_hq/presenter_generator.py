from __future__ import annotations

from pathlib import Path

import base64
import time
import httpx

from text2video.aws.s3 import S3Storage
from text2video.config import Settings, get_runtime_path
from text2video.commercial_hq.public_endpoints import RunpodPublicEndpointClient


DEFAULT_NEGATIVE_PROMPT = (
    "extra fingers, distorted hands, duplicate bottle, malformed product label, "
    "cropped face, bad anatomy, blurry, low quality, deformed smile, extra limbs"
)


def generate_presenter_image(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    prompt: str,
    product_image_key: str,
) -> dict:
    if not settings.runpod_api_key:
        raise ValueError("RUNPOD_API_KEY is not configured for presenter generation")

    storage = S3Storage(settings)
    output_key = f"presenters/{project_id}/{shot_id}.png"
    public_client = RunpodPublicEndpointClient(settings)
    product_image_url = storage.create_presigned_download(product_image_key, expires_in=3600)["url"]
    completed_payload = public_client.generate_nano_banana_2_edit(
        prompt=prompt,
        images=[product_image_url],
        resolution="1k",
        output_format="png",
        enable_safety_checker=True,
    )

    local_path = get_runtime_path(settings, "presenters", project_id, Path(output_key).name)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_presenter_output(
        payload=completed_payload or {},
        target_path=local_path,
    )

    if resolved["source"] == "remote_url":
        download_remote_file(resolved["url"], local_path)
        storage.upload_file(str(local_path), output_key)
    elif resolved["source"] == "inline_bytes":
        local_path.write_bytes(resolved["bytes"])
        storage.upload_file(str(local_path), output_key)
    else:
        raise RuntimeError(f"Nano Banana did not return a usable presenter image payload: {completed_payload}")

    return {
        "s3_key": output_key,
        "local_path": str(local_path),
        "download_url": storage.create_presigned_download(output_key, expires_in=3600)["url"],
    }


def resolve_presenter_output(payload: dict, target_path: Path) -> dict:
    output = payload.get("output")

    if isinstance(output, str):
        if output.startswith("http"):
            return {"source": "remote_url", "url": output}
        maybe_bytes = decode_base64_bytes(output)
        if maybe_bytes is not None:
            return {"source": "inline_bytes", "bytes": maybe_bytes}

    if isinstance(output, list):
        for item in output:
            if isinstance(item, str):
                if item.startswith("http"):
                    return {"source": "remote_url", "url": item}
                maybe_bytes = decode_base64_bytes(item)
                if maybe_bytes is not None:
                    return {"source": "inline_bytes", "bytes": maybe_bytes}
            if isinstance(item, dict):
                resolved = resolve_presenter_output({"output": item}, target_path)
                if resolved:
                    return resolved

    if isinstance(output, dict):
        for key in ("image_url", "url", "result", "image", "file_url"):
            value = output.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return {"source": "remote_url", "url": value}
        for key in ("image_base64", "base64", "data"):
            value = output.get(key)
            if isinstance(value, str):
                maybe_bytes = decode_base64_bytes(value)
                if maybe_bytes is not None:
                    return {"source": "inline_bytes", "bytes": maybe_bytes}

    raise RuntimeError(f"Could not resolve SDXL presenter output payload: {payload}")


def decode_base64_bytes(value: str) -> bytes | None:
    candidate = value
    if "," in candidate and candidate.strip().startswith("data:"):
        candidate = candidate.split(",", 1)[1]
    try:
        return base64.b64decode(candidate, validate=True)
    except Exception:
        return None


def download_remote_file(url: str, target_path: Path) -> Path:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.stream("GET", url, timeout=None) as response:
                response.raise_for_status()
                with target_path.open("wb") as file_handle:
                    for chunk in response.iter_bytes():
                        file_handle.write(chunk)
            return target_path
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(2 * (attempt + 1))
    raise last_error if last_error else RuntimeError(f"Failed to download presenter asset from {url}")

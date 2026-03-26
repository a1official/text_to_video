from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SdxlGenerateRequest(BaseModel):
    project_id: str
    shot_id: str
    prompt: str
    output_key: str
    upload_url: str
    width: int = 1280
    height: int = 704
    negative_prompt: str = ""
    num_inference_steps: int = 30
    guidance_scale: float = 7.0


class SdxlGenerateResponse(BaseModel):
    output_type: str = "keyframe_image"
    s3_key: str
    backend: str = "sdxl"
    resolution: str
    notes: str = ""


class WanGenerateRequest(BaseModel):
    project_id: str
    shot_id: str
    prompt: str
    source_image_key: str
    source_image_url: str
    output_key: str
    upload_url: str
    size: str = "1280*704"
    frame_num: int = 81
    sample_steps: int = 30
    sample_guide_scale: float = 5.0
    sample_shift: float | None = None
    seed: int = 0


class WanGenerateResponse(BaseModel):
    output_type: str = "render_segment"
    s3_key: str
    backend: str = "wan"
    resolution: str = "1280x704"
    fps: int = 24
    notes: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    sdxl_loaded: bool = False
    wan_repo_present: bool = False


class InferenceJobAccepted(BaseModel):
    job_id: str
    status: str = "queued"


class InferenceJobStatus(BaseModel):
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str = ""

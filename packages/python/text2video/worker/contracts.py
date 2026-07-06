from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BaseWorkerPayload(BaseModel):
    project_id: str
    shot_id: str


class RenderWorkerPayload(BaseWorkerPayload):
    sequence_index: int | None = None
    summary: str = ""
    prompt: str
    camera: str = ""
    appearance_prompt: str = ""
    motion_prompt: str = ""
    camera_prompt: str = ""
    duration_sec: int = 5
    shot_type: str
    backend_hint: Literal["wan", "humo", "ltx", "nano_banana_2_edit", "veo"]
    quality_tier: Literal["preview", "hero"] = "preview"
    audio_mode: str = "ambience"
    render_mode: str = "t2v"
    keyframe_output_key: str = ""
    source_image_key: str = ""
    depends_on_job_id: str = ""
    preview_output_key: str = ""
    preview_job_id: str = ""
    promotion_target: str = ""
    escalation_reason: str = ""
    continuity: list[str] = Field(default_factory=list)


class StitchSegmentRef(BaseModel):
    shot_id: str
    sequence_index: int | None = None
    job_id: str = ""
    job_type: str = ""
    backend_hint: str = "wan"
    duration_sec: int = 5
    transition: str = "hard_cut"
    output_key: str = ""


class StitchWorkerPayload(BaseModel):
    project_id: str
    scene_id: str
    manifest_sk: str
    output_key: str
    audio_key: str = ""
    continuity: list[str] = Field(default_factory=list)
    segments: list[StitchSegmentRef] = Field(default_factory=list)


class WorkerExecutionResult(BaseModel):
    status: Literal["completed", "failed"] = "completed"
    output_type: str
    s3_key: str
    duration_sec: int | None = None
    fps: int | None = None
    resolution: str | None = None
    backend: str | None = None
    seed: int | None = None
    manifest_ref: str | None = None
    notes: str | None = None

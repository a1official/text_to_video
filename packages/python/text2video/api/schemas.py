from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str
    created_by: str
    style_profile: str | None = None


class PlanRequest(BaseModel):
    project_id: str
    prompt: str
    references: list[dict[str, Any]] = Field(default_factory=list)


class PersistedPlanResponse(BaseModel):
    project_id: str
    summary: str
    continuity: list[str]
    shots: list[dict[str, Any]]


class CreateJobsFromPlanRequest(BaseModel):
    priority: int = 100
    include_continuity: bool = True


class ProjectJobsResponse(BaseModel):
    project_id: str
    jobs: list[dict[str, Any]]


class CreateStitchPlanRequest(BaseModel):
    scene_id: str = "scene001"
    output_prefix: str = "stitched"
    output_filename: str = "scene001.mp4"
    priority: int = 90


class StitchManifestResponse(BaseModel):
    project_id: str
    manifest: dict[str, Any]
    stitch_job: dict[str, Any] | None = None


class WorkerResultRequest(BaseModel):
    worker_id: str
    status: str = "completed"
    output_type: str
    s3_key: str
    duration_sec: int | None = None
    fps: int | None = None
    resolution: str | None = None
    backend: str | None = None
    seed: int | None = None
    manifest_ref: str | None = None
    notes: str | None = None


class ProjectOutputsResponse(BaseModel):
    project_id: str
    outputs: list[dict[str, Any]]


class SignedUploadRequest(BaseModel):
    project_id: str
    filename: str
    prefix: str = "uploads"
    expires_in: int = 3600


class CreateJobRequest(BaseModel):
    project_id: str
    shot_id: str | None = None
    job_type: str
    worker_type: str = "general"
    priority: int = 100
    payload: dict[str, Any] = Field(default_factory=dict)


class CommercialHQRequest(BaseModel):
    project_id: str
    product_image_key: str
    presenter_image_key: str = ""
    brief_mode: Literal["quick", "detailed"] = "quick"
    product_name: str = ""
    product_category: str = ""
    product_description: str = ""
    target_audience: str = ""
    key_benefits: list[str] = Field(default_factory=list)
    brand_tone: str = "Premium, trustworthy, English-language commercial"
    call_to_action: str = ""
    additional_notes: str = ""
    prompt: str = (
        "Create a premium stitched shampoo commercial with better presenter quality. "
        "Use a confident man speaking directly to camera in premium studio lighting, "
        "intercut with luxury product beauty shots and a strong packshot ending."
    )
    max_shots: int = 5
    width: int = 768
    height: int = 512
    num_inference_steps: int = 8
    guidance_scale: float = 3.0
    seed: int = 42
    output_key: str = ""
    voice_id: str = "Matthew"
    voice_engine: str = "neural"


class CommercialHQResponse(BaseModel):
    project_id: str
    summary: str
    concept: str
    voiceover_script: str
    supers: list[Any] = Field(default_factory=list)
    music_direction: str
    shots: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    segment_debug: list[dict[str, Any]] = Field(default_factory=list)
    master_voiceover_key: str = ""
    stitched_output_key: str
    stitched_output_uri: str
    stitched_local_path: str

from typing import Any

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

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


class StoryPipelineRequest(BaseModel):
    title: str = "Story pipeline test"
    created_by: str = "lambda"
    prompt: str
    voice_id: str = "Matthew"
    language_code: str = "en-IN"
    priority: int = 100
    image_count: int = 5
    duration_sec: int = 5
    create_stitch_plan: bool = True
    approval_required: bool = True


class StoryPipelineResponse(BaseModel):
    project_id: str
    project: dict[str, Any]
    plan: dict[str, Any]
    voiceover_script: str = ""
    tts: dict[str, Any]
    jobs: list[dict[str, Any]]
    approval_required: bool = True
    review_state: str = "awaiting_review"


class LambdaStoryPipelineResponse(BaseModel):
    function_name: str
    invoked_at: str
    request: StoryPipelineRequest
    result: dict[str, Any]


class GenerateTtsRequest(BaseModel):
    project_id: str
    script_text: str = ""
    voice_id: str = "Matthew"
    language_code: str = "en-IN"
    shot_id: str = "voiceover"
    output_prefix: str = "audio"


class GenerateTtsResponse(BaseModel):
    project_id: str
    shot_id: str
    audio_key: str
    audio_uri: str
    output: dict[str, Any]


class CreateStitchPlanRequest(BaseModel):
    scene_id: str = "scene001"
    output_prefix: str = "stitched"
    output_filename: str = "scene001.mp4"
    priority: int = 90


class PollProjectRequest(BaseModel):
    scene_id: str = "scene001"
    output_prefix: str = "stitched"
    output_filename: str = "scene001.mp4"


class StoryReviewRegenerateRequest(BaseModel):
    shot_id: str
    edit_prompt: str = ""
    priority: int = 100


class StoryReviewApproveRequest(BaseModel):
    approved_shot_ids: list[str] = Field(default_factory=list)
    generate_voiceover: bool = True
    priority: int = 100


class StoryReviewShotResponse(BaseModel):
    shot_id: str
    sequence_index: int | None = None
    duration_sec: int = 5
    review_status: str = "pending_review"
    appearance_prompt: str = ""
    motion_prompt: str = ""
    camera_prompt: str = ""
    edit_prompt: str = ""
    latest_output_key: str = ""
    latest_output_url: str = ""
    latest_output_type: str = ""
    approved_for_render: bool = False


class StoryReviewResponse(BaseModel):
    project_id: str
    project: dict[str, Any]
    review_state: str = "awaiting_review"
    approval_required: bool = True
    shots: list[StoryReviewShotResponse] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)


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
        "Create a premium stitched English commercial with better presenter quality. "
        "Use a confident presenter speaking directly to camera in premium studio lighting, "
        "intercut with luxury product hero shots and a strong closing packshot."
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


class ProductUnderstandingRequest(BaseModel):
    project_id: str
    product_image_key: str
    product_name: str = ""
    product_category: str = ""


class ProductUnderstandingResponse(BaseModel):
    project_id: str
    product_image_key: str
    product_analysis: dict[str, Any] = Field(default_factory=dict)
    product_brief: dict[str, Any] = Field(default_factory=dict)


class CommercialHQResponse(BaseModel):
    project_id: str
    summary: str
    concept: str
    voiceover_script: str
    supers: list[Any] = Field(default_factory=list)
    music_direction: str
    shots: list[dict[str, Any]] = Field(default_factory=list)
    product_brief: dict[str, Any] = Field(default_factory=dict)
    product_analysis: dict[str, Any] = Field(default_factory=dict)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    segment_debug: list[dict[str, Any]] = Field(default_factory=list)
    master_voiceover_key: str = ""
    stitched_output_key: str
    stitched_output_uri: str
    stitched_local_path: str


class CommercialNextgenRequest(BaseModel):
    project_id: str
    product_image_key: str
    brief_mode: Literal["quick", "detailed"] = "quick"
    product_name: str = ""
    product_category: str = ""
    product_description: str = ""
    target_audience: str = ""
    key_benefits: list[str] = Field(default_factory=list)
    brand_tone: str = "Premium, cinematic, English-language commercial"
    call_to_action: str = ""
    additional_notes: str = ""
    prompt: str = (
        "Create a premium stitched English commercial with cinematic range, stronger emotional build, "
        "and polished product storytelling."
    )
    width: int = 768
    height: int = 512
    seed: int = 42
    output_key: str = ""
    voice_id: str = "Matthew"
    voice_engine: str = "neural"


class CommercialNextgenResponse(BaseModel):
    project_id: str
    summary: str
    concept: str
    voiceover_script: str
    supers: list[Any] = Field(default_factory=list)
    music_direction: str
    shots: list[dict[str, Any]] = Field(default_factory=list)
    product_brief: dict[str, Any] = Field(default_factory=dict)
    product_analysis: dict[str, Any] = Field(default_factory=dict)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    segment_debug: list[dict[str, Any]] = Field(default_factory=list)
    master_voiceover_key: str = ""
    background_music_key: str = ""
    stitched_output_key: str
    stitched_output_uri: str
    stitched_local_path: str


class CommercialNvidiaRequest(BaseModel):
    project_id: str
    product_image_key: str
    brief_mode: Literal["quick", "detailed"] = "quick"
    product_name: str = ""
    product_category: str = ""
    product_description: str = ""
    target_audience: str = ""
    key_benefits: list[str] = Field(default_factory=list)
    brand_tone: str = "Premium, cinematic, English-language commercial"
    call_to_action: str = ""
    additional_notes: str = ""
    prompt: str = (
        "Create a premium stitched English commercial with clean product storytelling and cinematic product beauty shots."
    )
    width: int = 768
    height: int = 512
    seed: int = 42
    output_key: str = ""
    voice_id: str = "Matthew"
    voice_engine: str = "neural"


class CommercialNvidiaResponse(BaseModel):
    project_id: str
    summary: str
    concept: str
    voiceover_script: str
    supers: list[Any] = Field(default_factory=list)
    music_direction: str
    shots: list[dict[str, Any]] = Field(default_factory=list)
    product_brief: dict[str, Any] = Field(default_factory=dict)
    product_analysis: dict[str, Any] = Field(default_factory=dict)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    segment_debug: list[dict[str, Any]] = Field(default_factory=list)
    master_voiceover_key: str = ""
    stitched_output_key: str
    stitched_output_uri: str
    stitched_local_path: str


class CommercialReplicateRequest(BaseModel):
    project_id: str
    product_image_key: str
    brief_mode: Literal["quick", "detailed"] = "quick"
    product_name: str = ""
    product_category: str = ""
    product_description: str = ""
    target_audience: str = ""
    key_benefits: list[str] = Field(default_factory=list)
    brand_tone: str = "Premium, cinematic, English-language commercial"
    call_to_action: str = ""
    additional_notes: str = ""
    prompt: str = (
        "Create a premium stitched English commercial with cinematic product storytelling and polished product beauty shots."
    )
    width: int = 768
    height: int = 512
    seed: int = 42
    output_key: str = ""
    voice_id: str = "Matthew"


class CommercialReplicateResponse(BaseModel):
    project_id: str
    summary: str
    concept: str
    voiceover_script: str
    supers: list[Any] = Field(default_factory=list)
    music_direction: str
    shots: list[dict[str, Any]] = Field(default_factory=list)
    product_brief: dict[str, Any] = Field(default_factory=dict)
    product_analysis: dict[str, Any] = Field(default_factory=dict)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    segment_debug: list[dict[str, Any]] = Field(default_factory=list)
    master_voiceover_key: str = ""
    stitched_output_key: str
    stitched_output_uri: str
    stitched_local_path: str


class TalkingHeadRequest(BaseModel):
    project_id: str
    presenter_image_key: str
    topic: str = ""
    script: str = ""
    context: str = ""
    tone: str = "Professional, engaging, conversational"
    target_audience: str = "General audience"
    gender: Literal["male", "female"] = "male"
    sarvam_speaker: str = "aditya"
    language_code: str = "hi-IN"
    resolution: str = "480p"
    output_key: str = ""




class TalkingHeadResponse(BaseModel):
    project_id: str
    script: str
    key_points: list[str]
    duration_hint_sec: int
    speaker: str
    gender: str
    audio_key: str
    presenter_image_key: str
    output_key: str
    output_uri: str
    infinitetalk_cost: float | None = None
    local_video_path: str

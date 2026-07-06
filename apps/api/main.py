import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from text2video.api.schemas import (
    CommercialHQRequest,
    CommercialHQResponse,
    CommercialNvidiaRequest,
    CommercialNvidiaResponse,
    CommercialNextgenRequest,
    CommercialNextgenResponse,
    CommercialReplicateRequest,
    CommercialReplicateResponse,

    CreateJobsFromPlanRequest,
    CreateJobRequest,
    CreateProjectRequest,
    CreateStitchPlanRequest,
    LambdaStoryPipelineResponse,
    StoryPipelineRequest,
    StoryPipelineResponse,
    PollProjectRequest,
    PersistedPlanResponse,
    PlanRequest,
    ProductUnderstandingRequest,
    ProductUnderstandingResponse,
    ProjectJobsResponse,
    ProjectOutputsResponse,
    SignedUploadRequest,
    StitchManifestResponse,
    TalkingHeadRequest,
    TalkingHeadResponse,
    WorkerResultRequest,
)


from text2video.aws.dynamo import DynamoProjectStore
from text2video.aws.queue import DynamoJobQueue
from text2video.aws.s3 import S3Storage
from text2video.aws.session import build_boto3_session
from text2video.bedrock.planner import ShotPlanner
from text2video.commercial_hq.pipeline import run_hq_commercial
from text2video.commercial_replicate.pipeline import run_replicate_commercial
from text2video.commercial_nvidia.pipeline import run_nvidia_commercial
from text2video.commercial_nextgen.pipeline import run_nextgen_commercial
from text2video.commercial_hq.product_understanding import analyze_product_image, enrich_product_brief
from text2video.orchestrator.control_plane import (
    create_jobs_from_plan_workflow,
    create_project_workflow,
    create_stitch_plan_workflow,
    plan_project_workflow,
    run_story_pipeline_workflow,
    poll_project_workflow,
)
from text2video.talking_head.pipeline import run_talking_head_pipeline
from text2video.config import get_settings



settings = get_settings()
app = FastAPI(title="Text 2 Video API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

web_dir = Path("apps/web")
if web_dir.exists():
    app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="ui")

runtime_dir = Path(settings.runtime_root)
runtime_dir.mkdir(parents=True, exist_ok=True)



project_store = DynamoProjectStore(settings)
job_queue = DynamoJobQueue(settings)
planner = ShotPlanner(settings)
s3_storage = S3Storage(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> FileResponse:
    index_path = Path("apps/web/index.html")
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return FileResponse(index_path)


@app.post("/projects")
def create_project(request: CreateProjectRequest) -> dict[str, str]:
    return create_project_workflow(
        settings=settings,
        title=request.title,
        created_by=request.created_by,
        style_profile=request.style_profile,
    )


@app.post("/planner/plan")
def plan_project(request: PlanRequest) -> PersistedPlanResponse:
    return PersistedPlanResponse(
        **plan_project_workflow(
            settings=settings,
            project_id=request.project_id,
            prompt=request.prompt,
            references=request.references,
        )
    )


@app.post("/planner/test")
def test_planner(request: PlanRequest) -> dict:
    return {
        "mode": "bedrock",
        "model_id": settings.bedrock_model_id,
        "result": planner.plan_project(
            project_id=request.project_id,
            prompt=request.prompt,
            references=request.references,
        ),
    }


@app.post("/pipelines/story", response_model=StoryPipelineResponse)
def run_story_pipeline(request: StoryPipelineRequest) -> StoryPipelineResponse:
    result = run_story_pipeline_workflow(
        settings=settings,
        title=request.title,
        created_by=request.created_by,
        prompt=request.prompt,
        voice_id=request.voice_id,
        language_code=request.language_code,
        priority=request.priority,
    )
    return StoryPipelineResponse(**result)


@app.post("/pipelines/story/lambda", response_model=LambdaStoryPipelineResponse)
def run_story_pipeline_lambda(request: StoryPipelineRequest) -> LambdaStoryPipelineResponse:
    lambda_client = build_boto3_session(settings).client("lambda")
    payload = {
        "action": "run_story_pipeline",
        "title": request.title,
        "created_by": request.created_by,
        "prompt": request.prompt,
        "voice_id": request.voice_id,
        "language_code": request.language_code,
        "priority": request.priority,
    }
    response = lambda_client.invoke(
        FunctionName=settings.lambda_story_orchestrator_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8") if response.get("Payload") else "{}"
    result = json.loads(raw or "{}")
    if response.get("FunctionError"):
        raise HTTPException(status_code=502, detail=result or "Lambda invocation failed")
    return LambdaStoryPipelineResponse(
        function_name=settings.lambda_story_orchestrator_function_name,
        invoked_at=datetime.now(timezone.utc).isoformat(),
        request=request,
        result=result,
    )


@app.post("/commercials/hq", response_model=CommercialHQResponse)
def create_commercial_hq(request: CommercialHQRequest) -> CommercialHQResponse:
    result = run_hq_commercial(
        settings=settings,
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        presenter_image_key=request.presenter_image_key,
        brief_mode=request.brief_mode,
        product_name=request.product_name,
        product_category=request.product_category,
        product_description=request.product_description,
        target_audience=request.target_audience,
        key_benefits=request.key_benefits,
        brand_tone=request.brand_tone,
        call_to_action=request.call_to_action,
        additional_notes=request.additional_notes,
        prompt=request.prompt,
        max_shots=request.max_shots,
        width=request.width,
        height=request.height,
        num_inference_steps=request.num_inference_steps,
        guidance_scale=request.guidance_scale,
        seed=request.seed,
        output_key=request.output_key,
        voice_id=request.voice_id,
        voice_engine=request.voice_engine,
    )
    return CommercialHQResponse(**result)


@app.post("/commercials/nextgen", response_model=CommercialNextgenResponse)
def create_commercial_nextgen(request: CommercialNextgenRequest) -> CommercialNextgenResponse:
    result = run_nextgen_commercial(
        settings=settings,
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        brief_mode=request.brief_mode,
        product_name=request.product_name,
        product_category=request.product_category,
        product_description=request.product_description,
        target_audience=request.target_audience,
        key_benefits=request.key_benefits,
        brand_tone=request.brand_tone,
        call_to_action=request.call_to_action,
        additional_notes=request.additional_notes,
        prompt=request.prompt,
        width=request.width,
        height=request.height,
        seed=request.seed,
        output_key=request.output_key,
        voice_id=request.voice_id,
        voice_engine=request.voice_engine,
    )
    return CommercialNextgenResponse(**result)


@app.post("/commercials/nvidia", response_model=CommercialNvidiaResponse)
def create_commercial_nvidia(request: CommercialNvidiaRequest) -> CommercialNvidiaResponse:
    result = run_nvidia_commercial(
        settings=settings,
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        brief_mode=request.brief_mode,
        product_name=request.product_name,
        product_category=request.product_category,
        product_description=request.product_description,
        target_audience=request.target_audience,
        key_benefits=request.key_benefits,
        brand_tone=request.brand_tone,
        call_to_action=request.call_to_action,
        additional_notes=request.additional_notes,
        prompt=request.prompt,
        width=request.width,
        height=request.height,
        seed=request.seed,
        output_key=request.output_key,
        voice_id=request.voice_id,
        voice_engine=request.voice_engine,
    )
    return CommercialNvidiaResponse(**result)


@app.post("/commercials/replicate", response_model=CommercialReplicateResponse)
def create_commercial_replicate(request: CommercialReplicateRequest) -> CommercialReplicateResponse:
    result = run_replicate_commercial(
        settings=settings,
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        brief_mode=request.brief_mode,
        product_name=request.product_name,
        product_category=request.product_category,
        product_description=request.product_description,
        target_audience=request.target_audience,
        key_benefits=request.key_benefits,
        brand_tone=request.brand_tone,
        call_to_action=request.call_to_action,
        additional_notes=request.additional_notes,
        prompt=request.prompt,
        width=request.width,
        height=request.height,
        seed=request.seed,
        output_key=request.output_key,
        voice_id=request.voice_id,
    )
    return CommercialReplicateResponse(**result)


@app.post("/pipelines/talking-head", response_model=TalkingHeadResponse)
def create_talking_head(request: TalkingHeadRequest) -> TalkingHeadResponse:
    result = run_talking_head_pipeline(
        settings=settings,
        project_id=request.project_id,
        presenter_image_key=request.presenter_image_key,
        topic=request.topic,
        script=request.script,
        context=request.context,
        tone=request.tone,
        target_audience=request.target_audience,
        gender=request.gender,
        sarvam_speaker=request.sarvam_speaker,
        language_code=request.language_code,
        resolution=request.resolution,
        output_key=request.output_key,

    )

    return TalkingHeadResponse(**result)








@app.post("/commercials/hq/analyze-product", response_model=ProductUnderstandingResponse)
def analyze_commercial_product(request: ProductUnderstandingRequest) -> ProductUnderstandingResponse:
    analysis = analyze_product_image(
        settings=settings,
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        user_product_name=request.product_name,
        user_product_category=request.product_category,
    )
    brief = enrich_product_brief(
        {
            "brief_mode": "quick",
            "product_name": (request.product_name or "").strip(),
            "product_category": (request.product_category or "").strip(),
            "product_description": "",
            "target_audience": "",
            "key_benefits": [],
            "brand_tone": "Premium, trustworthy, English-language commercial",
            "call_to_action": "",
            "additional_notes": "",
            "extra_direction": "",
            "presenter_profile": {},
        },
        analysis,
    )
    return ProductUnderstandingResponse(
        project_id=request.project_id,
        product_image_key=request.product_image_key,
        product_analysis=analysis,
        product_brief=brief,
    )


@app.get("/projects/{project_id}/shots")
def list_project_shots(project_id: str) -> dict[str, list[dict]]:
    project = project_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"shots": project_store.list_shots(project_id)}


@app.post("/projects/{project_id}/jobs/from-plan")
def create_jobs_from_plan(project_id: str, request: CreateJobsFromPlanRequest) -> ProjectJobsResponse:
    try:
        result = create_jobs_from_plan_workflow(
            settings=settings,
            project_id=project_id,
            priority=request.priority,
            include_continuity=request.include_continuity,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ProjectJobsResponse(project_id=project_id, jobs=result["jobs"])


@app.post("/projects/{project_id}/stitch-plan")
def create_stitch_plan(project_id: str, request: CreateStitchPlanRequest) -> StitchManifestResponse:
    try:
        result = create_stitch_plan_workflow(
            settings=settings,
            project_id=project_id,
            scene_id=request.scene_id,
            output_prefix=request.output_prefix,
            output_filename=request.output_filename,
            priority=request.priority,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return StitchManifestResponse(
        project_id=project_id,
        manifest=result["manifest"],
        stitch_job=result["stitch_job"],
    )


@app.post("/projects/{project_id}/poll")
def poll_project(project_id: str, request: PollProjectRequest) -> dict:
    try:
        return poll_project_workflow(
            settings=settings,
            project_id=project_id,
            scene_id=request.scene_id,
            output_prefix=request.output_prefix,
            output_filename=request.output_filename,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/assets/upload")
async def upload_local_asset(
    file: UploadFile = File(...),
    key: str = Form(...),
) -> dict[str, str]:
    runtime_dir = Path(settings.runtime_root)
    target_path = runtime_dir / key.lstrip("/")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    with target_path.open("wb") as buffer:
        import shutil
        shutil.copyfileobj(file.file, buffer)
        
    return {"status": "ok", "key": key, "uri": f"{settings.base_public_url}/assets/{key}"}


@app.post("/assets/signed-upload")

def create_signed_upload(request: SignedUploadRequest) -> dict[str, str]:
    if not settings.s3_bucket:
        raise HTTPException(status_code=400, detail="S3_BUCKET is not configured")
    key = s3_storage.make_key(
        project_id=request.project_id,
        prefix=request.prefix.strip("/"),
        filename=request.filename,
    )
    return s3_storage.create_presigned_upload(key=key, expires_in=request.expires_in)


@app.post("/jobs")
def create_job(request: CreateJobRequest) -> dict[str, str]:
    if request.job_type not in settings.allowed_job_types:
        raise HTTPException(status_code=400, detail="Unsupported job type")
    return job_queue.enqueue(
        project_id=request.project_id,
        shot_id=request.shot_id,
        job_type=request.job_type,
        worker_type=request.worker_type,
        payload=request.payload,
        priority=request.priority,
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/simulate-start")
def simulate_job_start(job_id: str, worker_id: str = "local-sim-worker") -> dict:
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_queue.force_start(job_id=job_id, worker_id=worker_id)


@app.post("/jobs/{job_id}/complete")
def complete_job(job_id: str, request: WorkerResultRequest) -> dict:
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    project_id = job["project_id"]
    output = project_store.save_output(
        project_id=project_id,
        shot_id=job.get("shot_id", ""),
        job_id=job_id,
        output={
            "output_type": request.output_type,
            "s3_key": request.s3_key,
            "duration_sec": request.duration_sec,
            "fps": request.fps,
            "resolution": request.resolution,
            "backend": request.backend or job.get("worker_type", ""),
            "seed": request.seed,
            "manifest_ref": request.manifest_ref,
            "notes": request.notes or "",
        },
    )

    job_queue.mark_complete(
        job_id=job_id,
        worker_id=request.worker_id,
        result={
            "output_id": output["output_id"],
            "output_type": output["output_type"],
            "s3_key": output["s3_key"],
            "backend": output.get("backend", ""),
        },
    )
    return {"job": job_queue.get_job(job_id), "output": output}


@app.get("/projects/{project_id}/jobs")
def list_project_jobs(project_id: str) -> ProjectJobsResponse:
    project = project_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectJobsResponse(project_id=project_id, jobs=job_queue.list_jobs_for_project(project_id))


@app.get("/projects/{project_id}/manifests")
def list_project_manifests(project_id: str) -> dict[str, list[dict]]:
    project = project_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"manifests": project_store.list_manifests(project_id)}


@app.get("/projects/{project_id}/outputs")
def list_project_outputs(project_id: str) -> ProjectOutputsResponse:
    project = project_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectOutputsResponse(project_id=project_id, outputs=project_store.list_outputs(project_id))


def _job_type_for_backend(backend_hint: str) -> str:
    if backend_hint == "humo":
        return "generate_segment_humo"
    if backend_hint == "ltx":
        return "generate_preview"
    return "generate_segment_wan"


def _worker_type_for_backend(backend_hint: str) -> str:
    if backend_hint in {"wan", "humo"}:
        return backend_hint
    return "general"


def _compose_motion_prompt(appearance_prompt: str, motion_prompt: str, camera_prompt: str) -> str:
    parts = [part.strip() for part in [appearance_prompt, motion_prompt, camera_prompt] if part and part.strip()]
    return ". ".join(parts)


app.mount("/assets", StaticFiles(directory=runtime_dir), name="assets")

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from text2video.api.schemas import (
    CreateJobsFromPlanRequest,
    CreateJobRequest,
    CreateProjectRequest,
    CreateStitchPlanRequest,
    PersistedPlanResponse,
    PlanRequest,
    ProjectJobsResponse,
    ProjectOutputsResponse,
    SignedUploadRequest,
    StitchManifestResponse,
    WorkerResultRequest,
)
from text2video.aws.dynamo import DynamoProjectStore
from text2video.aws.queue import DynamoJobQueue
from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.config import get_settings


settings = get_settings()
app = FastAPI(title="Text 2 Video API", version="0.1.0")

web_dir = Path("apps/web")
if web_dir.exists():
    app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="ui")

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
    project = project_store.create_project(
        title=request.title,
        created_by=request.created_by,
        style_profile=request.style_profile,
    )
    return project


@app.post("/planner/plan")
def plan_project(request: PlanRequest) -> PersistedPlanResponse:
    plan = planner.plan_project(
        project_id=request.project_id,
        prompt=request.prompt,
        references=request.references,
    )
    return PersistedPlanResponse(
        **project_store.save_plan(
            project_id=request.project_id,
            summary=plan["summary"],
            continuity=plan["continuity"],
            shots=plan["shots"],
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


@app.get("/projects/{project_id}/shots")
def list_project_shots(project_id: str) -> dict[str, list[dict]]:
    project = project_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"shots": project_store.list_shots(project_id)}


@app.post("/projects/{project_id}/jobs/from-plan")
def create_jobs_from_plan(project_id: str, request: CreateJobsFromPlanRequest) -> ProjectJobsResponse:
    try:
        plan_context = project_store.get_plan_context(project_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")

    shots = plan_context["shots"]
    if not shots:
        raise HTTPException(status_code=400, detail="No stored shots found for project")

    created_jobs = []
    for shot in shots:
        appearance_prompt = shot.get("appearance_prompt") or shot.get("prompt", "")
        motion_prompt = shot.get("motion_prompt") or shot.get("prompt", "")
        camera_prompt = shot.get("camera_prompt") or shot.get("camera", "")
        quality_tier = shot.get("quality_tier", "preview")
        payload = {
            "project_id": project_id,
            "shot_id": shot["shot_id"],
            "sequence_index": shot.get("sequence_index"),
            "summary": plan_context["summary"],
            "prompt": appearance_prompt,
            "camera": camera_prompt,
            "appearance_prompt": appearance_prompt,
            "motion_prompt": motion_prompt,
            "camera_prompt": camera_prompt,
            "duration_sec": shot.get("duration_sec", 5),
            "shot_type": shot.get("shot_type", "wide"),
            "backend_hint": shot.get("backend_hint", "wan"),
            "quality_tier": quality_tier,
            "audio_mode": shot.get("audio_mode", "ambience"),
        }
        if request.include_continuity:
            payload["continuity"] = plan_context["continuity"]

        keyframe_output_key = f"keyframes/{project_id}/{shot['shot_id']}.png"
        preview_output_key = f"previews/{project_id}/{shot['shot_id']}.mp4"
        render_backend = shot.get("backend_hint", "wan")
        use_reference_lane = render_backend in {"wan", "ltx"}

        if use_reference_lane:
            sdxl_job = job_queue.enqueue(
                project_id=project_id,
                shot_id=shot["shot_id"],
                job_type="generate_keyframe_sdxl",
                worker_type="wan",
                payload={
                    **payload,
                    "prompt": appearance_prompt,
                    "render_mode": "text_to_image",
                    "keyframe_output_key": keyframe_output_key,
                },
                priority=request.priority,
            )
            created_jobs.append(job_queue.get_job(sdxl_job["job_id"]))

            ltx_job = job_queue.enqueue(
                project_id=project_id,
                shot_id=shot["shot_id"],
                job_type="generate_preview",
                worker_type="general",
                payload={
                    **payload,
                    "prompt": _compose_motion_prompt(appearance_prompt, motion_prompt, camera_prompt),
                    "render_mode": "preview_i2v",
                    "source_image_key": keyframe_output_key,
                    "keyframe_output_key": keyframe_output_key,
                    "preview_output_key": preview_output_key,
                    "depends_on_job_id": sdxl_job["job_id"],
                    "promotion_target": "wan" if render_backend == "wan" else "",
                },
                priority=request.priority,
            )
            created_jobs.append(job_queue.get_job(ltx_job["job_id"]))

            if render_backend == "wan":
                wan_job = job_queue.enqueue(
                    project_id=project_id,
                    shot_id=shot["shot_id"],
                    job_type="generate_segment_wan",
                    worker_type="wan",
                    payload={
                        **payload,
                        "prompt": _compose_motion_prompt(appearance_prompt, motion_prompt, camera_prompt),
                        "render_mode": "ti2v",
                        "source_image_key": keyframe_output_key,
                        "keyframe_output_key": keyframe_output_key,
                        "preview_output_key": preview_output_key,
                        "preview_job_id": ltx_job["job_id"],
                        "depends_on_job_id": sdxl_job["job_id"],
                        "promotion_target": "wan",
                        "escalation_reason": "hero_lane",
                    },
                    priority=request.priority,
                )
                created_jobs.append(job_queue.get_job(wan_job["job_id"]))
            continue

        job = job_queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type=_job_type_for_backend(render_backend),
            worker_type=_worker_type_for_backend(render_backend),
            payload=payload,
            priority=request.priority,
        )
        created_jobs.append(job_queue.get_job(job["job_id"]))

    return ProjectJobsResponse(project_id=project_id, jobs=created_jobs)


@app.post("/projects/{project_id}/stitch-plan")
def create_stitch_plan(project_id: str, request: CreateStitchPlanRequest) -> StitchManifestResponse:
    try:
        plan_context = project_store.get_plan_context(project_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")

    shots = plan_context["shots"]
    if not shots:
        raise HTTPException(status_code=400, detail="No stored shots found for project")

    project_jobs = job_queue.list_jobs_for_project(project_id)
    render_jobs = [
        job
        for job in project_jobs
        if job.get("shot_id") and job.get("job_type") in {"generate_segment_wan", "generate_segment_humo", "generate_preview"}
    ]
    jobs_by_shot: dict[str, dict] = {}
    job_rank = {
        "generate_segment_wan": 3,
        "generate_segment_humo": 3,
        "generate_preview": 1,
    }
    for job in render_jobs:
        existing = jobs_by_shot.get(job["shot_id"])
        if not existing or job_rank.get(job["job_type"], 0) >= job_rank.get(existing["job_type"], 0):
            jobs_by_shot[job["shot_id"]] = job

    manifest_segments = []
    for shot in shots:
        selected_job = jobs_by_shot.get(shot["shot_id"], {})
        selected_job_type = selected_job.get("job_type", "")
        if selected_job_type == "generate_preview":
            output_key = f"previews/{project_id}/{shot['shot_id']}.mp4"
        elif selected_job:
            output_key = f"renders/{project_id}/{shot['shot_id']}.mp4"
        else:
            output_key = ""
        transition = "crossfade" if shot.get("shot_type") == "transition" else "hard_cut"
        manifest_segments.append(
            {
                "shot_id": shot["shot_id"],
                "sequence_index": shot.get("sequence_index"),
                "job_id": selected_job.get("job_id", ""),
                "job_type": selected_job_type,
                "backend_hint": shot.get("backend_hint", "wan"),
                "duration_sec": shot.get("duration_sec", 5),
                "transition": transition,
                "output_key": output_key,
            }
        )

    manifest = {
        "scene_id": request.scene_id,
        "summary": plan_context["summary"],
        "continuity": plan_context["continuity"],
        "output_key": f"{request.output_prefix.strip('/')}/{project_id}/{request.output_filename}",
        "segments": manifest_segments,
        "status": "planned",
    }
    saved_manifest = project_store.save_stitch_manifest(project_id, manifest)

    stitch_job = job_queue.enqueue(
        project_id=project_id,
        shot_id=request.scene_id,
        job_type="stitch_segments",
        worker_type="stitch",
        payload={
            "project_id": project_id,
            "scene_id": request.scene_id,
            "manifest_sk": saved_manifest["sk"],
            "output_key": manifest["output_key"],
            "segments": manifest_segments,
            "continuity": plan_context["continuity"],
        },
        priority=request.priority,
    )

    return StitchManifestResponse(
        project_id=project_id,
        manifest=saved_manifest,
        stitch_job=job_queue.get_job(stitch_job["job_id"]),
    )


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

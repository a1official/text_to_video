from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from text2video.aws.dynamo import DynamoProjectStore
from text2video.aws.queue import DynamoJobQueue
from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.config import Settings
from text2video.tts.sarvam import synthesize_sarvam_tts_mp3
from text2video.worker.runner import WorkerRunner


def create_project_workflow(*, settings: Settings, title: str, created_by: str, style_profile: str | None) -> dict:
    store = DynamoProjectStore(settings)
    project = store.create_project(title=title, created_by=created_by, style_profile=style_profile)
    store.set_project_status(project["project_id"], "created", workflow_state="project_created")
    return project


def plan_project_workflow(
    *,
    settings: Settings,
    project_id: str,
    prompt: str,
    references: list[dict[str, Any]],
    desired_shot_count: int | None = None,
    shot_duration_sec: int | None = None,
) -> dict:
    planner = ShotPlanner(settings)
    store = DynamoProjectStore(settings)
    plan = planner.plan_project(
        project_id=project_id,
        prompt=prompt,
        references=references,
        desired_shot_count=desired_shot_count,
        shot_duration_sec=shot_duration_sec,
    )
    saved = store.save_plan(
        project_id=project_id,
        summary=plan["summary"],
        continuity=plan["continuity"],
        shots=plan["shots"],
    )
    store.set_project_status(
        project_id,
        "planned",
        workflow_state="plan_persisted",
        shot_count=len(plan["shots"]),
        plan_summary=plan["summary"],
        continuity=plan["continuity"],
    )
    return saved


def create_jobs_from_plan_workflow(
    *,
    settings: Settings,
    project_id: str,
    priority: int = 100,
    include_continuity: bool = True,
) -> dict:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    plan_context = store.get_plan_context(project_id)
    shots = plan_context["shots"]
    if not shots:
        raise ValueError("No stored shots found for project")

    created_jobs: list[dict[str, Any]] = []
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
        if include_continuity:
            payload["continuity"] = plan_context["continuity"]

        keyframe_output_key = f"keyframes/{project_id}/{shot['shot_id']}.png"
        preview_output_key = f"previews/{project_id}/{shot['shot_id']}.mp4"
        render_backend = shot.get("backend_hint", "wan")
        use_reference_lane = render_backend in {"wan", "ltx"}

        if use_reference_lane:
            sdxl_job = queue.enqueue(
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
                priority=priority,
            )
            created_jobs.append(queue.get_job(sdxl_job["job_id"]))

            ltx_job = queue.enqueue(
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
                priority=priority,
            )
            created_jobs.append(queue.get_job(ltx_job["job_id"]))

            if render_backend == "wan":
                wan_job = queue.enqueue(
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
                    priority=priority,
                )
                created_jobs.append(queue.get_job(wan_job["job_id"]))
            continue

        job = queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type=_job_type_for_backend(render_backend),
            worker_type=_worker_type_for_backend(render_backend),
            payload=payload,
            priority=priority,
        )
        created_jobs.append(queue.get_job(job["job_id"]))

    store.set_project_status(project_id, "queued", workflow_state="jobs_queued", job_count=len(created_jobs))
    return {"project_id": project_id, "jobs": created_jobs}


def create_story_jobs_from_plan_workflow(
    *,
    settings: Settings,
    project_id: str,
    priority: int = 100,
    include_continuity: bool = True,
) -> dict:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    plan_context = store.get_plan_context(project_id)
    shots = plan_context["shots"]
    if not shots:
        raise ValueError("No stored shots found for project")

    created_jobs: list[dict[str, Any]] = []
    for shot in shots:
        appearance_prompt = shot.get("appearance_prompt") or shot.get("prompt", "")
        motion_prompt = shot.get("motion_prompt") or shot.get("prompt", "")
        camera_prompt = shot.get("camera_prompt") or shot.get("camera", "")
        quality_tier = shot.get("quality_tier", "preview")
        prompt_parts = [appearance_prompt, motion_prompt, camera_prompt, plan_context["summary"]]
        composite_prompt = ". ".join(part.strip() for part in prompt_parts if part and str(part).strip())

        if include_continuity:
            continuity = plan_context["continuity"]
        else:
            continuity = []

        keyframe_output_key = f"keyframes/{project_id}/{shot['shot_id']}.png"
        render_output_key = f"renders/{project_id}/{shot['shot_id']}.mp4"

        keyframe_job = queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type="generate_keyframe_nano_banana_2_edit",
            worker_type="general",
            payload={
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
                "backend_hint": "nano_banana_2_edit",
                "quality_tier": "hero" if shot.get("shot_type") in {"establishing", "wide", "hero_product"} else quality_tier,
                "audio_mode": shot.get("audio_mode", "ambience"),
                "keyframe_output_key": keyframe_output_key,
                "continuity": continuity,
            },
            priority=priority,
        )
        created_jobs.append(queue.get_job(keyframe_job["job_id"]))

        veo_job = queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type="generate_segment_veo",
            worker_type="general",
            payload={
                "project_id": project_id,
                "shot_id": shot["shot_id"],
                "sequence_index": shot.get("sequence_index"),
                "summary": plan_context["summary"],
                "prompt": composite_prompt,
                "camera": camera_prompt,
                "appearance_prompt": appearance_prompt,
                "motion_prompt": motion_prompt,
                "camera_prompt": camera_prompt,
                "duration_sec": shot.get("duration_sec", 5),
                "shot_type": shot.get("shot_type", "wide"),
                "backend_hint": "veo",
                "quality_tier": "hero",
                "audio_mode": shot.get("audio_mode", "ambience"),
                "source_image_key": keyframe_output_key,
                "preview_output_key": render_output_key,
                "depends_on_job_id": keyframe_job["job_id"],
                "continuity": continuity,
            },
            priority=priority,
        )
        created_jobs.append(queue.get_job(veo_job["job_id"]))

    store.set_project_status(
        project_id,
        "queued",
        workflow_state="story_jobs_queued",
        job_count=len(created_jobs),
        voiceover_script=str(store.get_project(project_id).get("voiceover_script") or plan_context["summary"]),
    )
    return {"project_id": project_id, "jobs": created_jobs}


def create_story_review_jobs_from_plan_workflow(
    *,
    settings: Settings,
    project_id: str,
    priority: int = 100,
    include_continuity: bool = True,
) -> dict:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    plan_context = store.get_plan_context(project_id)
    shots = plan_context["shots"]
    if not shots:
        raise ValueError("No stored shots found for project")

    created_jobs: list[dict[str, Any]] = []
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
            "backend_hint": "nano_banana_2_edit",
            "quality_tier": "hero" if shot.get("shot_type") in {"establishing", "wide", "hero_product"} else quality_tier,
            "audio_mode": shot.get("audio_mode", "ambience"),
            "keyframe_output_key": f"keyframes/{project_id}/{shot['shot_id']}.png",
            "continuity": plan_context["continuity"] if include_continuity else [],
        }

        keyframe_job = queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type="generate_keyframe_nano_banana_2_edit",
            worker_type="general",
            payload=payload,
            priority=priority,
        )
        created_jobs.append(queue.get_job(keyframe_job["job_id"]))

    store.set_project_status(
        project_id,
        "review_pending",
        workflow_state="awaiting_review",
        review_required=True,
        approval_required=True,
        job_count=len(created_jobs),
    )
    return {"project_id": project_id, "jobs": created_jobs}


def run_story_pipeline_workflow(
    *,
    settings: Settings,
    title: str,
    created_by: str,
    prompt: str,
    voice_id: str = "Matthew",
    language_code: str = "en-IN",
    priority: int = 100,
    image_count: int = 5,
    duration_sec: int = 5,
    approval_required: bool = True,
) -> dict:
    project = create_project_workflow(
        settings=settings,
        title=title,
        created_by=created_by,
        style_profile="original story / lambda orchestration",
    )
    project_id = project["project_id"]
    plan = plan_project_workflow(
        settings=settings,
        project_id=project_id,
        prompt=prompt,
        references=[],
        desired_shot_count=max(image_count, 1),
        shot_duration_sec=max(duration_sec, 1),
    )
    story_script = generate_story_voiceover_script(
        settings=settings,
        prompt=prompt,
        summary=str(plan.get("summary") or prompt).strip(),
        continuity=list(plan.get("continuity") or []),
        target_duration_sec=max(image_count, 1) * max(duration_sec, 1),
    )
    store = DynamoProjectStore(settings)
    store.set_project_status(
        project_id,
        "review_pending",
        workflow_state="awaiting_review",
        approval_required=approval_required,
        review_required=approval_required,
        image_count=max(image_count, 1),
        duration_sec=max(duration_sec, 1),
        voiceover_script=story_script,
        voice_id=voice_id,
        voiceover_language_code=language_code,
        story_prompt=prompt,
        plan_summary=str(plan.get("summary") or prompt).strip(),
    )
    tts = {
        "status": "deferred",
        "message": "Voiceover generation is deferred until the user approves the image set.",
    }
    jobs = create_story_review_jobs_from_plan_workflow(
        settings=settings,
        project_id=project_id,
        priority=priority,
        include_continuity=True,
    )
    return {
        "project_id": project_id,
        "project": project,
        "plan": plan,
        "voiceover_script": story_script,
        "tts": tts,
        "jobs": jobs["jobs"],
        "approval_required": approval_required,
        "review_state": "awaiting_review",
    }


def generate_story_voiceover_script(
    *,
    settings: Settings,
    prompt: str,
    summary: str,
    continuity: list[str],
    target_duration_sec: int | None = None,
) -> str:
    system_prompt = (
        "You are a senior story editor writing a polished narration script for a cinematic video. "
        "Return valid JSON only with a single key: voiceover_script. "
        "The voiceover_script must be a short continuous narration in natural English. "
        "It should feel premium, specific, and emotionally coherent. "
        "Aim for roughly 110 to 130 words unless the target runtime suggests otherwise. "
        "Avoid repeating the prompt verbatim. "
        "Do not use markdown or bullet points."
    )
    continuity_text = "; ".join(item.strip() for item in continuity if str(item).strip())
    user_payload = {
        "prompt": prompt,
        "summary": summary,
        "continuity": continuity_text,
        "target_duration_sec": target_duration_sec,
    }

    try:
        planner = ShotPlanner(settings)
        response = planner.client.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": min(max(settings.bedrock_temperature, 0.1), 0.6),
                "maxTokens": min(settings.bedrock_max_tokens, 1200),
            },
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": json.dumps(user_payload)}]}],
        )
        text = response["output"]["message"]["content"][0]["text"]
        payload = _parse_json_text(text)
        script = str(payload.get("voiceover_script") or "").strip()
        if script:
            return script
    except Exception:
        pass

    fallback_bits = [summary.strip() or prompt.strip()]
    if continuity_text:
        fallback_bits.append(f"Story beats: {continuity_text}.")
    if target_duration_sec:
        fallback_bits.append(f"Target runtime: about {target_duration_sec} seconds.")
    fallback_bits.append("A new choice begins here.")
    return " ".join(part for part in fallback_bits if part).strip()


def generate_tts_workflow(
    *,
    settings: Settings,
    project_id: str,
    script_text: str,
    voice_id: str,
    language_code: str = "en-IN",
    shot_id: str = "voiceover",
    output_prefix: str = "audio",
) -> dict:
    store = DynamoProjectStore(settings)
    storage = S3Storage(settings)
    project = store.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} was not found")

    normalized_script = " ".join((script_text or "").split()).strip()
    if not normalized_script:
        normalized_script = " ".join(
            str(project.get("voiceover_script") or project.get("prompt") or project.get("plan_summary") or "").split()
        ).strip()
    if not normalized_script:
        raise ValueError("A narration script is required to generate TTS.")

    audio_path = synthesize_sarvam_tts_mp3(
        settings=settings,
        project_id=project_id,
        shot_id=shot_id,
        script_text=normalized_script,
        voice_id=voice_id,
        language_code=language_code,
    )
    audio_key = storage.make_key(project_id, output_prefix, f"{shot_id}.mp3")
    audio_uri = storage.upload_file(str(audio_path), audio_key)

    output = store.save_output(
        project_id=project_id,
        shot_id=shot_id,
        job_id=f"tts-{project_id}",
        output={
            "output_type": "voiceover_audio",
            "provider": "sarvam",
            "voice_id": voice_id,
            "language_code": language_code,
            "s3_key": audio_key,
            "s3_uri": audio_uri,
            "local_path": str(audio_path),
            "text": normalized_script,
        },
    )
    store.set_project_status(
        project_id,
        project.get("status", "planned"),
        workflow_state="tts_generated",
        voiceover_key=audio_key,
        voiceover_uri=audio_uri,
        voice_id=voice_id,
        voiceover_language_code=language_code,
        voiceover_script=normalized_script,
    )
    return {
        "project_id": project_id,
        "shot_id": shot_id,
        "audio_key": audio_key,
        "audio_uri": audio_uri,
        "output": output,
    }


def build_story_review_view(*, settings: Settings, project_id: str) -> dict[str, Any]:
    store = DynamoProjectStore(settings)
    storage = S3Storage(settings)
    project = store.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} was not found")

    outputs = store.list_outputs(project_id)
    shots = store.list_shots(project_id)
    latest_outputs_by_shot: dict[str, dict[str, Any]] = {}
    for output in outputs:
        shot_id = str(output.get("shot_id") or "").strip()
        if not shot_id:
            continue
        latest_outputs_by_shot[shot_id] = output

    review_shots: list[dict[str, Any]] = []
    for shot in shots:
        latest_output = latest_outputs_by_shot.get(shot["shot_id"], {})
        output_key = str(latest_output.get("s3_key") or "").strip()
        review_shots.append(
            {
                "shot_id": shot["shot_id"],
                "sequence_index": shot.get("sequence_index"),
                "duration_sec": int(shot.get("duration_sec") or 5),
                "review_status": str(shot.get("review_status") or "pending_review"),
                "appearance_prompt": str(shot.get("appearance_prompt") or shot.get("prompt") or ""),
                "motion_prompt": str(shot.get("motion_prompt") or shot.get("prompt") or ""),
                "camera_prompt": str(shot.get("camera_prompt") or shot.get("camera") or ""),
                "edit_prompt": str(shot.get("edit_prompt") or ""),
                "latest_output_key": output_key,
                "latest_output_url": storage.create_presigned_download(output_key)["url"] if output_key else "",
                "latest_output_type": str(latest_output.get("output_type") or ""),
                "approved_for_render": bool(shot.get("approved_for_render", False)),
            }
        )

    return {
        "project_id": project_id,
        "project": project,
        "review_state": str(project.get("workflow_state") or project.get("status") or "awaiting_review"),
        "approval_required": bool(project.get("approval_required", True)),
        "shots": review_shots,
        "outputs": outputs,
    }


def regenerate_story_shot_workflow(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    edit_prompt: str,
    priority: int = 100,
) -> dict[str, Any]:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    shot = store.get_shot(project_id, shot_id)
    if not shot:
        raise ValueError(f"Shot {shot_id} was not found for project {project_id}")

    base_prompt = str(shot.get("appearance_prompt") or shot.get("prompt") or "").strip()
    motion_prompt = str(shot.get("motion_prompt") or "").strip()
    camera_prompt = str(shot.get("camera_prompt") or "").strip()
    combined_edit = " ".join(part for part in [base_prompt, edit_prompt.strip()] if part).strip()
    updated_shot = store.update_shot_metadata(
        project_id,
        shot_id,
        {
            "appearance_prompt": combined_edit or base_prompt,
            "edit_prompt": edit_prompt.strip(),
            "review_status": "pending_review",
            "approved_for_render": False,
        },
    )

    job = queue.enqueue(
        project_id=project_id,
        shot_id=shot_id,
        job_type="generate_keyframe_nano_banana_2_edit",
        worker_type="general",
        payload={
            "project_id": project_id,
            "shot_id": shot_id,
            "sequence_index": shot.get("sequence_index"),
            "summary": str(store.get_plan_context(project_id).get("summary") or ""),
            "prompt": combined_edit or base_prompt,
            "camera": camera_prompt,
            "appearance_prompt": combined_edit or base_prompt,
            "motion_prompt": motion_prompt,
            "camera_prompt": camera_prompt,
            "duration_sec": int(shot.get("duration_sec") or 5),
            "shot_type": str(shot.get("shot_type") or "wide"),
            "backend_hint": "nano_banana_2_edit",
            "quality_tier": "hero" if shot.get("shot_type") in {"establishing", "wide", "hero_product"} else str(shot.get("quality_tier") or "preview"),
            "audio_mode": str(shot.get("audio_mode") or "ambience"),
            "keyframe_output_key": f"keyframes/{project_id}/{shot_id}.png",
            "continuity": list(store.get_plan_context(project_id).get("continuity") or []),
        },
        priority=priority,
    )
    store.set_project_status(
        project_id,
        str(store.get_project(project_id).get("status") or "review_pending"),
        workflow_state="awaiting_review",
        review_required=True,
    )
    return {"project_id": project_id, "shot": updated_shot, "job": queue.get_job(job["job_id"])}


def approve_story_review_workflow(
    *,
    settings: Settings,
    project_id: str,
    approved_shot_ids: list[str] | None = None,
    generate_voiceover: bool = True,
    priority: int = 100,
) -> dict[str, Any]:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    project = store.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} was not found")

    shots = store.list_shots(project_id)
    outputs = store.list_outputs(project_id)
    latest_outputs_by_shot: dict[str, dict[str, Any]] = {}
    for output in outputs:
        shot_id = str(output.get("shot_id") or "").strip()
        if shot_id:
            latest_outputs_by_shot[shot_id] = output

    approved_set = set(approved_shot_ids or [])
    if not approved_set:
        approved_set = {shot["shot_id"] for shot in shots}

    created_jobs: list[dict[str, Any]] = []
    for shot in shots:
        if shot["shot_id"] not in approved_set:
            continue
        latest_output = latest_outputs_by_shot.get(shot["shot_id"])
        if not latest_output:
            raise ValueError(f"Shot {shot['shot_id']} does not have a generated image to send to Veo")
        source_image_key = str(latest_output.get("s3_key") or "").strip()
        if not source_image_key:
            raise ValueError(f"Shot {shot['shot_id']} is missing a source image key")

        store.update_shot_metadata(
            project_id,
            shot["shot_id"],
            {
                "review_status": "approved",
                "approved_for_render": True,
            },
        )

        veo_job = queue.enqueue(
            project_id=project_id,
            shot_id=shot["shot_id"],
            job_type="generate_segment_veo",
            worker_type="general",
            payload={
                "project_id": project_id,
                "shot_id": shot["shot_id"],
                "sequence_index": shot.get("sequence_index"),
                "summary": str(project.get("plan_summary") or project.get("story_prompt") or ""),
                "prompt": str(shot.get("appearance_prompt") or shot.get("prompt") or ""),
                "camera": str(shot.get("camera_prompt") or shot.get("camera") or ""),
                "appearance_prompt": str(shot.get("appearance_prompt") or shot.get("prompt") or ""),
                "motion_prompt": str(shot.get("motion_prompt") or shot.get("prompt") or ""),
                "camera_prompt": str(shot.get("camera_prompt") or shot.get("camera") or ""),
                "duration_sec": int(shot.get("duration_sec") or 5),
                "shot_type": str(shot.get("shot_type") or "wide"),
                "backend_hint": "veo",
                "quality_tier": "hero",
                "audio_mode": str(shot.get("audio_mode") or "ambience"),
                "source_image_key": source_image_key,
                "preview_output_key": f"renders/{project_id}/{shot['shot_id']}.mp4",
                "continuity": list(store.get_plan_context(project_id).get("continuity") or []),
            },
            priority=priority,
        )
        created_jobs.append(queue.get_job(veo_job["job_id"]))

    voiceover_result: dict[str, Any] = {}
    if generate_voiceover and not any(output.get("output_type") in {"voiceover_audio", "tts_audio"} for output in outputs):
        voiceover_result = generate_tts_workflow(
            settings=settings,
            project_id=project_id,
            script_text=str(project.get("voiceover_script") or project.get("story_prompt") or ""),
            voice_id=str(project.get("voice_id") or "Matthew"),
            language_code=str(project.get("voiceover_language_code") or "en-IN"),
            shot_id="story-voiceover",
            output_prefix="audio",
        )

    store.set_project_status(
        project_id,
        "rendering",
        workflow_state="veo_queued",
        review_required=True,
        approval_required=True,
        approved_for_render=True,
        approved_shot_count=len(created_jobs),
    )
    return {
        "project_id": project_id,
        "jobs": created_jobs,
        "voiceover": voiceover_result,
        "review": build_story_review_view(settings=settings, project_id=project_id),
    }


def _parse_json_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return json.loads(cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start : end + 1])

    raise ValueError(f"Could not parse JSON from Bedrock response: {cleaned[:300]}")


def create_stitch_plan_workflow(
    *,
    settings: Settings,
    project_id: str,
    scene_id: str = "scene001",
    output_prefix: str = "stitched",
    output_filename: str = "scene001.mp4",
    priority: int = 90,
) -> dict:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    plan_context = store.get_plan_context(project_id)
    shots = plan_context["shots"]
    if not shots:
        raise ValueError("No stored shots found for project")

    project_jobs = queue.list_jobs_for_project(project_id)
    render_jobs = [
        job
        for job in project_jobs
        if job.get("shot_id")
        and job.get("job_type") in {"generate_segment_wan", "generate_segment_humo", "generate_segment_veo", "generate_preview"}
    ]
    jobs_by_shot: dict[str, dict] = {}
    job_rank = {
        "generate_segment_wan": 3,
        "generate_segment_humo": 3,
        "generate_segment_veo": 4,
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
        "scene_id": scene_id,
        "summary": plan_context["summary"],
        "continuity": plan_context["continuity"],
        "output_key": f"{output_prefix.strip('/')}/{project_id}/{output_filename}",
        "segments": manifest_segments,
        "status": "planned",
    }
    saved_manifest = store.save_stitch_manifest(project_id, manifest)

    stitch_job = queue.enqueue(
        project_id=project_id,
        shot_id=scene_id,
        job_type="stitch_segments",
        worker_type="stitch",
        payload={
        "project_id": project_id,
        "scene_id": scene_id,
        "manifest_sk": saved_manifest["sk"],
        "output_key": manifest["output_key"],
        "audio_key": str(project.get("voiceover_key") or project.get("voiceover_audio_key") or ""),
        "segments": manifest_segments,
        "continuity": plan_context["continuity"],
    },
        priority=priority,
    )
    store.set_project_status(project_id, "stitching", workflow_state="stitch_queued", stitch_job_id=stitch_job["job_id"])
    return {
        "project_id": project_id,
        "manifest": saved_manifest,
        "stitch_job": queue.get_job(stitch_job["job_id"]),
    }


def poll_project_workflow(
    *,
    settings: Settings,
    project_id: str,
    scene_id: str = "scene001",
    output_prefix: str = "stitched",
    output_filename: str = "scene001.mp4",
) -> dict:
    store = DynamoProjectStore(settings)
    queue = DynamoJobQueue(settings)
    project = store.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} was not found")

    shots = store.list_shots(project_id)
    jobs = queue.list_jobs_for_project(project_id)
    if shots and not jobs and project.get("status") in {"created", "planned"}:
        created = create_jobs_from_plan_workflow(settings=settings, project_id=project_id)
        return {
            "project_id": project_id,
            "state": "jobs_queued",
            "detail": "Queued render jobs from the stored plan.",
            "jobs": created["jobs"],
        }

    active_states = {"pending", "running"}
    if any(job.get("status") in active_states for job in jobs):
        store.set_project_status(project_id, project.get("status", "queued"), workflow_state="polling_active")
        return {
            "project_id": project_id,
            "state": "active",
            "detail": "Jobs are still running.",
            "job_count": len(jobs),
        }

    failed_jobs = [job for job in jobs if job.get("status") == "failed"]
    if failed_jobs:
        store.set_project_status(project_id, "failed", workflow_state="job_failed")
        return {
            "project_id": project_id,
            "state": "failed",
            "detail": "One or more jobs failed.",
            "failed_jobs": failed_jobs,
        }

    if str(project.get("workflow_state") or "").strip() == "awaiting_review" and not project.get("approved_for_render"):
        review = build_story_review_view(settings=settings, project_id=project_id)
        review["state"] = "awaiting_review"
        review["detail"] = "Image generation is complete. Approval is required before Veo rendering starts."
        return review

    voiceover_script = str(project.get("voiceover_script") or "").strip()
    voiceover_outputs = [
        output
        for output in store.list_outputs(project_id)
        if output.get("output_type") in {"voiceover_audio", "tts_audio"}
    ]
    if voiceover_script and not voiceover_outputs:
        generated_tts = generate_tts_workflow(
            settings=settings,
            project_id=project_id,
            script_text=voiceover_script,
            voice_id=str(project.get("voice_id") or "Matthew"),
            language_code=str(project.get("voiceover_language_code") or "en-IN"),
        )
        return {
            "project_id": project_id,
            "state": "tts_generated",
            "detail": "Generated Sarvam narration for the project.",
            **generated_tts,
        }

    manifests = store.list_manifests(project_id)
    if not manifests and shots:
        stitched = create_stitch_plan_workflow(
            settings=settings,
            project_id=project_id,
            scene_id=scene_id,
            output_prefix=output_prefix,
            output_filename=output_filename,
        )
        return {
            "project_id": project_id,
            "state": "stitch_queued",
            "detail": "Queued the stitch job after all render jobs completed.",
            **stitched,
        }

    stitch_jobs = [job for job in jobs if job.get("job_type") == "stitch_segments"]
    completed_stitch_jobs = [job for job in stitch_jobs if job.get("status") == "completed"]
    if completed_stitch_jobs:
        completed_stitch_job = completed_stitch_jobs[-1]
        final_key = str((completed_stitch_job.get("result") or {}).get("s3_key") or "")
        final_uri = f"s3://{settings.s3_bucket}/{final_key}" if final_key else ""
        final_url = S3Storage(settings).create_presigned_download(final_key)["url"] if final_key else ""
        store.set_project_status(
            project_id,
            "complete",
            workflow_state="render_complete",
            last_completed_job_id=completed_stitch_job.get("job_id", ""),
            final_output_key=final_key,
            final_output_uri=final_uri,
        )
        return {
            "project_id": project_id,
            "state": "complete",
            "detail": "The stitch job completed successfully.",
            "final_output_key": final_key,
            "final_output_uri": final_uri,
            "final_download_url": final_url,
            "outputs": store.list_outputs(project_id),
        }

    return {
        "project_id": project_id,
        "state": "idle",
        "detail": "No new action required.",
        "job_count": len(jobs),
        "manifest_count": len(manifests),
    }


def process_worker_once_workflow(
    *, settings: Settings, worker_type: str = "general", project_id: str | None = None
) -> dict[str, Any]:
    if project_id:
        return process_project_once_workflow(settings=settings, project_id=project_id, worker_type=worker_type)
    original_worker_type = settings.worker_type
    settings.worker_type = worker_type  # type: ignore[misc]
    processed = WorkerRunner(settings).run_once()
    settings.worker_type = original_worker_type  # type: ignore[misc]
    return {"worker_type": worker_type, "processed": processed}


def process_project_once_workflow(*, settings: Settings, project_id: str, worker_type: str = "general") -> dict[str, Any]:
    queue = DynamoJobQueue(settings)
    jobs = queue.list_jobs_for_project(project_id)
    adapters = WorkerRunner(settings).adapters

    for job in jobs:
        if job.get("status") != "pending":
            continue
        if str(job.get("worker_type") or "") not in {worker_type, "general"}:
            continue
        if not WorkerRunner(settings)._job_is_ready(job):
            continue
        job_id = job["job_id"]
        if not queue.try_acquire(job_id, settings.worker_id):
            continue
        try:
            adapter = adapters.get(job["job_type"])
            if not adapter:
                result = {
                    "message": f"Stub worker completed {job['job_type']}",
                    "worker_type": worker_type,
                }
                queue.mark_complete(job_id, settings.worker_id, result)
                return {"worker_type": worker_type, "project_id": project_id, "processed": True, "job_id": job_id}
            result = adapter.execute(job)
            completion_result = {
                "output_type": result.output_type,
                "s3_key": result.s3_key,
                "backend": result.backend,
                "duration_sec": result.duration_sec,
                "fps": result.fps,
                "resolution": result.resolution,
                "seed": result.seed,
                "manifest_ref": result.manifest_ref,
                "notes": result.notes,
            }
            queue.mark_complete(job_id, settings.worker_id, completion_result)
            return {"worker_type": worker_type, "project_id": project_id, "processed": True, "job_id": job_id}
        except Exception as exc:  # pragma: no cover - scaffold safety
            queue.mark_failed(job_id, settings.worker_id, str(exc))
            return {"worker_type": worker_type, "project_id": project_id, "processed": False, "job_id": job_id, "error": str(exc)}

    return {"worker_type": worker_type, "project_id": project_id, "processed": False}


def lambda_handler(event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    settings = Settings()
    action = str(event.get("action") or event.get("type") or "").strip().lower()

    if action == "create_project":
        return create_project_workflow(
            settings=settings,
            title=str(event.get("title") or "Untitled project"),
            created_by=str(event.get("created_by") or "lambda"),
            style_profile=event.get("style_profile"),
        )
    if action == "plan_project":
        return plan_project_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            prompt=str(event.get("prompt") or ""),
            references=list(event.get("references") or []),
        )
    if action == "queue_jobs":
        return create_jobs_from_plan_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            priority=int(event.get("priority") or 100),
            include_continuity=bool(event.get("include_continuity", True)),
        )
    if action == "queue_story_jobs":
        return create_story_jobs_from_plan_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            priority=int(event.get("priority") or 100),
            include_continuity=bool(event.get("include_continuity", True)),
        )
    if action == "generate_tts":
        return generate_tts_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            script_text=str(event.get("script_text") or ""),
            voice_id=str(event.get("voice_id") or "Matthew"),
            language_code=str(event.get("language_code") or "en-IN"),
            shot_id=str(event.get("shot_id") or "voiceover"),
            output_prefix=str(event.get("output_prefix") or "audio"),
        )
    if action == "stitch_project":
        return create_stitch_plan_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            scene_id=str(event.get("scene_id") or "scene001"),
            output_prefix=str(event.get("output_prefix") or "stitched"),
            output_filename=str(event.get("output_filename") or "scene001.mp4"),
            priority=int(event.get("priority") or 90),
        )
    if action == "poll_project":
        return poll_project_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            scene_id=str(event.get("scene_id") or "scene001"),
            output_prefix=str(event.get("output_prefix") or "stitched"),
            output_filename=str(event.get("output_filename") or "scene001.mp4"),
        )
    if action == "process_worker_once":
        return process_worker_once_workflow(
            settings=settings,
            worker_type=str(event.get("worker_type") or "general"),
            project_id=str(event.get("project_id") or "").strip() or None,
        )
    if action == "run_story_pipeline":
        return run_story_pipeline_workflow(
            settings=settings,
            title=str(event.get("title") or "Story pipeline test"),
            created_by=str(event.get("created_by") or "lambda"),
            prompt=str(event.get("prompt") or ""),
            voice_id=str(event.get("voice_id") or "Matthew"),
            language_code=str(event.get("language_code") or "en-IN"),
            priority=int(event.get("priority") or 100),
            image_count=int(event.get("image_count") or 5),
            duration_sec=int(event.get("duration_sec") or 5),
            approval_required=bool(event.get("approval_required", True)),
        )
    if action == "approve_story_review":
        return approve_story_review_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            approved_shot_ids=list(event.get("approved_shot_ids") or []),
            generate_voiceover=bool(event.get("generate_voiceover", True)),
            priority=int(event.get("priority") or 100),
        )
    if action == "regenerate_story_review":
        return regenerate_story_shot_workflow(
            settings=settings,
            project_id=str(event["project_id"]),
            shot_id=str(event["shot_id"]),
            edit_prompt=str(event.get("edit_prompt") or ""),
            priority=int(event.get("priority") or 100),
        )

    raise ValueError(f"Unsupported action: {action or '<missing>'}")


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

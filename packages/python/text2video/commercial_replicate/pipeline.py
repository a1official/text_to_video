from __future__ import annotations

import json

from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.commercial_hq.pipeline import (
    build_presenter_profile,
    download_remote_file,
    mux_master_voiceover,
    normalize_hq_script,
    normalize_product_brief,
    normalize_segment_video,
    post_process_commercial,
    save_hq_checkpoint,
)
from text2video.commercial_hq.product_understanding import analyze_product_image, enrich_product_brief
from text2video.commercial_replicate.elevenlabs_tts import synthesize_elevenlabs_voice_line
from text2video.commercial_replicate.replicate_client import ReplicateClient, extract_replicate_output_url
from text2video.config import Settings, get_runtime_path
from text2video.worker.contracts import StitchSegmentRef, StitchWorkerPayload
from text2video.worker.stitch import run_ffmpeg_stitch


REPLICATE_COMMERCIAL_SYSTEM_PROMPT = """You are a premium commercial director building a cinematic stitched advertisement from one product packshot.

Return valid JSON only with:
- concept
- voiceover_script
- supers
- music_direction
- shots

Rules:
- Use exactly 5 product-focused cinematic shots.
- Do not use a presenter.
- Build one cohesive English commercial, not disconnected clips.
- Every shot must preserve the uploaded product identity.
- Each shot must include:
  - shot_id
  - shot_type
  - duration_sec
  - appearance_prompt
  - motion_prompt
  - camera_prompt
  - voiceover_line
  - on_screen_text
- Shot order:
  - hero reveal
  - premium detail
  - texture or usage detail
  - benefit payoff
  - closing packshot
- Keep claims grounded in the structured product brief.
- No markdown.
- No explanation outside JSON."""


def run_replicate_commercial(
    *,
    settings: Settings,
    project_id: str,
    product_image_key: str,
    brief_mode: str,
    product_name: str,
    product_category: str,
    product_description: str,
    target_audience: str,
    key_benefits: list[str],
    brand_tone: str,
    call_to_action: str,
    additional_notes: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    output_key: str,
    voice_id: str,
) -> dict:
    del seed
    storage = S3Storage(settings)
    planner = ShotPlanner(settings)
    replicate = ReplicateClient(settings)
    presenter_profile = build_presenter_profile(voice_id)

    product_brief = normalize_product_brief(
        brief_mode=brief_mode,
        product_name=product_name,
        product_category=product_category,
        product_description=product_description,
        target_audience=target_audience,
        key_benefits=key_benefits,
        brand_tone=brand_tone,
        call_to_action=call_to_action,
        additional_notes=additional_notes,
        extra_direction=prompt,
        presenter_profile=presenter_profile,
    )
    product_analysis = analyze_product_image(
        settings=settings,
        project_id=project_id,
        product_image_key=product_image_key,
        user_product_name=product_brief["product_name"],
        user_product_category=product_brief["product_category"],
    )
    product_brief = enrich_product_brief(product_brief, product_analysis)

    commercial_brief = json.dumps(
        {
            "project_id": project_id,
            "prompt": prompt,
            "product_brief": product_brief,
            "product_analysis": product_analysis,
        },
        indent=2,
    )
    commercial_package = planner._parse_json_response(
        planner.client.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": settings.bedrock_temperature,
                "maxTokens": settings.bedrock_max_tokens,
            },
            system=[{"text": REPLICATE_COMMERCIAL_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": commercial_brief}]}],
        )["output"]["message"]["content"][0]["text"]
    )
    plan = normalize_hq_script(commercial_package)
    shots = force_product_only_arc(plan["shots"])

    outputs_root = get_runtime_path(settings, "outputs", project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)
    product_image_url = storage.create_presigned_download(product_image_key, expires_in=3600)["url"]

    segments: list[StitchSegmentRef] = []
    segment_debug: list[dict] = []

    for shot in shots:
        shot_id = shot["shot_id"]
        payload = replicate.generate_luma_video(
            prompt=build_replicate_prompt(shot=shot, product_brief=product_brief),
            start_image_url=product_image_url,
            duration_seconds=min(max(int(shot.get("duration_sec", 5)), 5), 5),
            aspect_ratio="16:9",
        )
        remote_video_url = extract_replicate_output_url(payload)
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        download_remote_file(remote_video_url, local_segment_path)
        normalize_segment_video(source_path=local_segment_path, width=width, height=height)
        preview_key = f"previews/{project_id}/{shot_id}.mp4"
        storage.upload_file(str(local_segment_path), preview_key)
        segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"replicate-{shot_id}",
                job_type="generate_preview",
                backend_hint="replicate-luma",
                duration_sec=shot.get("duration_sec", 5),
                transition="hard_cut",
                output_key=preview_key,
            )
        )
        segment_debug.append(
            {
                "shot_id": shot_id,
                "engine": "replicate-luma",
                "model": settings.replicate_video_model,
                "remote_video_url": remote_video_url,
                "output_key": preview_key,
                "raw_output": payload,
            }
        )

    stitched_output_key = output_key or f"stitched/{project_id}/commercial-replicate.mp4"
    stitched_path = run_ffmpeg_stitch(
        settings,
        StitchWorkerPayload(
            project_id=project_id,
            scene_id="scene001",
            manifest_sk=f"MANIFEST#{project_id}",
            output_key=stitched_output_key,
            continuity=plan["continuity"],
            segments=segments,
        ),
    )
    stitched_path = post_process_commercial(
        settings=settings,
        storage=storage,
        project_id=project_id,
        stitched_path=stitched_path,
        shots=shots,
        supers=plan["supers"],
        product_image_key=product_image_key,
        product_name=product_brief["product_name"],
    )
    master_voiceover_path = synthesize_elevenlabs_voice_line(
        settings=settings,
        project_id=project_id,
        shot_id="master-voiceover",
        script_text=plan["voiceover_script"],
        voice_id=voice_id,
    )
    stitched_path = mux_master_voiceover(video_path=stitched_path, audio_path=master_voiceover_path)
    master_voiceover_key = f"audio/{project_id}/master-voiceover.mp3"
    storage.upload_file(str(master_voiceover_path), master_voiceover_key)
    stitched_uri = storage.upload_file(str(stitched_path), stitched_output_key)
    save_hq_checkpoint(
        settings,
        project_id,
        {
            "pipeline": "replicate",
            "product_brief": product_brief,
            "product_analysis": product_analysis,
            "plan": plan,
            "segments": [segment.model_dump() for segment in segments],
            "segment_debug": segment_debug,
            "master_voiceover_key": master_voiceover_key,
            "stitched_output_key": stitched_output_key,
            "stitched_output_uri": stitched_uri,
            "stitched_local_path": str(stitched_path),
        },
    )
    return {
        "project_id": project_id,
        "summary": plan["summary"],
        "concept": plan["concept"],
        "voiceover_script": plan["voiceover_script"],
        "supers": plan["supers"],
        "music_direction": plan["music_direction"],
        "shots": shots,
        "product_brief": product_brief,
        "product_analysis": product_analysis,
        "segments": [segment.model_dump() for segment in segments],
        "segment_debug": segment_debug,
        "master_voiceover_key": master_voiceover_key,
        "stitched_output_key": stitched_output_key,
        "stitched_output_uri": stitched_uri,
        "stitched_local_path": str(stitched_path),
    }


def force_product_only_arc(shots: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    desired_types = ["hero_product", "hero_product", "benefit_cutaway", "benefit_cutaway", "endcard"]
    role_hints = [
        "hero reveal",
        "premium detail",
        "texture or usage detail",
        "benefit payoff",
        "closing packshot",
    ]
    base = shots[:5] if shots else []
    while len(base) < 5 and base:
        base.append(dict(base[-1]))
    if not base:
        raise RuntimeError("Commercial planner did not return enough shots for Replicate pipeline")
    for index, shot in enumerate(base[:5], start=1):
        cloned = dict(shot)
        cloned["shot_id"] = f"shot{index:03d}"
        cloned["sequence_index"] = index
        cloned["shot_type"] = desired_types[index - 1]
        cloned["role_hint"] = role_hints[index - 1]
        cloned["audio_mode"] = "ambience"
        cloned["duration_sec"] = 5
        normalized.append(cloned)
    return normalized


def build_replicate_prompt(*, shot: dict, product_brief: dict) -> str:
    parts = [
        shot.get("appearance_prompt", "").strip(),
        shot.get("motion_prompt", "").strip(),
        shot.get("camera_prompt", "").strip(),
        (
            f"Premium cinematic product commercial for {product_brief['product_name']} in the "
            f"{product_brief['product_category']} category."
        ),
        "Preserve the exact product identity, packaging silhouette, and brand colors from the source image.",
        "Commercial-grade lighting, elegant reflections, subtle luxury VFX, polished ad-film motion, no presenter, no extra text.",
    ]
    return ". ".join(part for part in parts if part)

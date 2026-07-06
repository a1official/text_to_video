from __future__ import annotations

import json
import subprocess
from pathlib import Path

from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.commercial_hq.pipeline import (
    build_presenter_profile,
    normalize_hq_script,
    normalize_product_brief,
    normalize_segment_video,
    post_process_commercial,
    save_hq_checkpoint,
    synthesize_voice_line,
)
from text2video.commercial_hq.product_understanding import (
    analyze_product_image,
    download_product_image,
    enrich_product_brief,
)
from text2video.commercial_nvidia.nvidia_client import NvidiaCatalogClient, write_nvidia_video_payload
from text2video.config import Settings, get_runtime_path
from text2video.worker.contracts import StitchSegmentRef, StitchWorkerPayload
from text2video.worker.stitch import run_ffmpeg_stitch


NVIDIA_COMMERCIAL_SYSTEM_PROMPT = """You are a premium commercial director planning a polished stitched advertisement.

Return valid JSON only with:
- concept
- voiceover_script
- supers
- music_direction
- shots

Rules:
- Use exactly 5 shots.
- Use exactly 1 talking presenter opening shot and 4 product-focused cinematic shots.
- The product must remain visually consistent with the uploaded packshot.
- Each shot must include:
  - shot_id
  - shot_type
  - duration_sec
  - appearance_prompt
  - motion_prompt
  - camera_prompt
  - voiceover_line
  - on_screen_text
- Keep everything in English.
- Make the commercial cohesive and premium.
- No markdown.
- No explanation outside JSON."""


def run_nvidia_commercial(
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
    voice_engine: str,
) -> dict:
    storage = S3Storage(settings)
    planner = ShotPlanner(settings)
    nvidia = NvidiaCatalogClient(settings)
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

    commercial_prompt = json.dumps(
        {
            "project_id": project_id,
            "prompt": prompt,
            "product_brief": product_brief,
            "product_analysis": product_analysis,
            "presenter_profile": presenter_profile,
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
            system=[{"text": NVIDIA_COMMERCIAL_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": commercial_prompt}]}],
        )["output"]["message"]["content"][0]["text"]
    )
    plan = normalize_hq_script(commercial_package)

    outputs_root = get_runtime_path(settings, "outputs", project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)
    local_product_image_path = download_product_image(
        settings=settings,
        storage=storage,
        project_id=project_id,
        product_image_key=product_image_key,
    )

    segments: list[StitchSegmentRef] = []
    segment_debug: list[dict] = []

    for shot in plan["shots"]:
        shot_id = shot["shot_id"]
        payload = nvidia.generate_video(
            image_path=local_product_image_path,
            seed=seed or None,
        )
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        write_nvidia_video_payload(payload, local_segment_path)
        normalize_segment_video(source_path=local_segment_path, width=width, height=height)
        preview_key = f"previews/{project_id}/{shot_id}.mp4"
        storage.upload_file(str(local_segment_path), preview_key)
        segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"nvidia-{shot_id}",
                job_type="generate_preview",
                backend_hint="nvidia",
                duration_sec=shot.get("duration_sec", 4),
                transition="hard_cut",
                output_key=preview_key,
            )
        )
        segment_debug.append(
            {
                "shot_id": shot_id,
                "engine": "nvidia",
                "model": settings.nvidia_video_model,
                "output_key": preview_key,
                "raw_output": payload,
            }
        )

    stitched_output_key = output_key or f"stitched/{project_id}/commercial-nvidia.mp4"
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
        shots=plan["shots"],
        supers=plan["supers"],
        product_image_key=product_image_key,
        product_name=product_brief["product_name"],
    )
    voiceover_path = synthesize_voice_line(
        settings=settings,
        project_id=project_id,
        shot_id="master-voiceover",
        script_text=plan["voiceover_script"],
        voice_id=voice_id,
        engine=voice_engine,
    )
    stitched_path = mux_audio_to_video(video_path=stitched_path, audio_path=voiceover_path)

    master_voiceover_key = f"audio/{project_id}/master-voiceover.mp3"
    storage.upload_file(str(voiceover_path), master_voiceover_key)
    stitched_uri = storage.upload_file(str(stitched_path), stitched_output_key)
    save_hq_checkpoint(
        settings,
        project_id,
        {
            "pipeline": "nvidia",
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
        "shots": plan["shots"],
        "product_brief": product_brief,
        "product_analysis": product_analysis,
        "segments": [segment.model_dump() for segment in segments],
        "segment_debug": segment_debug,
        "master_voiceover_key": master_voiceover_key,
        "stitched_output_key": stitched_output_key,
        "stitched_output_uri": stitched_uri,
        "stitched_local_path": str(stitched_path),
    }
def mux_audio_to_video(*, video_path: Path, audio_path: Path) -> Path:
    output_path = video_path.with_name(f"{video_path.stem}.vo{video_path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-af",
        "apad",
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    return output_path

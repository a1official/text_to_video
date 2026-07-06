from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx

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
    synthesize_voice_line,
)
from text2video.commercial_hq.product_understanding import analyze_product_image, enrich_product_brief
from text2video.commercial_nextgen.openrouter_client import (
    OpenRouterClient,
    extract_audio_url,
    extract_video_url,
)
from text2video.config import Settings, get_runtime_path
from text2video.worker.contracts import StitchSegmentRef, StitchWorkerPayload
from text2video.worker.stitch import run_ffmpeg_stitch


NEXTGEN_SYSTEM_PROMPT = """You are a commercial director orchestrating a premium product advertisement across multiple generative video models.

Return valid JSON only with:
- concept
- voiceover_script
- supers
- music_direction
- shots

Rules:
- Use exactly 5 shots.
- Shot order:
  - cinematic opener
  - product reveal
  - texture/detail beat
  - benefit/lifestyle payoff
  - cinematic closer
- Use `sora` for shot001 and shot005.
- Use `wan` for shot002, shot003, and shot004.
- Each shot must include:
  - shot_id
  - shot_type
  - duration_sec
  - prompt
  - camera
  - motion
  - voiceover_line
  - on_screen_text
- Write a single cohesive commercial, not disconnected clips.
- Use the product brief as the source of truth.
- Keep everything in English.
- No markdown.
- No explanation outside JSON."""


def run_nextgen_commercial(
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
    openrouter = OpenRouterClient(settings)
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

    planner = ShotPlanner(settings)
    commercial_package = planner._parse_json_response(
        planner.client.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": settings.bedrock_temperature,
                "maxTokens": settings.bedrock_max_tokens,
            },
            system=[{"text": NEXTGEN_SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "project_id": project_id,
                                    "prompt": prompt,
                                    "product_brief": product_brief,
                                    "product_analysis": product_analysis,
                                    "presenter_profile": presenter_profile,
                                },
                                indent=2,
                            )
                        }
                    ],
                }
            ],
        )["output"]["message"]["content"][0]["text"]
    )
    plan = normalize_nextgen_script(commercial_package)

    outputs_root = get_runtime_path(settings, "outputs", project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)
    product_image_url = storage.create_presigned_download(product_image_key, expires_in=3600)["url"]

    segments: list[StitchSegmentRef] = []
    segment_debug: list[dict] = []

    for shot in plan["shots"]:
        shot_id = shot["shot_id"]
        model = "openai/sora-2-pro" if shot["backend_hint"] == "sora" else "alibaba/wan-2.6"
        payload = openrouter.generate_video(
            model=model,
            prompt=build_video_prompt(shot=shot, product_brief=product_brief, presenter_profile=presenter_profile),
            image_url=product_image_url,
            duration_seconds=int(shot.get("duration_sec", 4)),
            width=width,
            height=height,
        )
        remote_video_url = extract_video_url(payload)
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        download_remote_file(remote_video_url, local_segment_path)
        normalize_segment_video(source_path=local_segment_path, width=width, height=height)
        preview_key = f"previews/{project_id}/{shot_id}.mp4"
        storage.upload_file(str(local_segment_path), preview_key)
        segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"{shot['backend_hint']}-{shot_id}",
                job_type="generate_preview",
                backend_hint=shot["backend_hint"],
                duration_sec=shot.get("duration_sec", 4),
                transition="hard_cut",
                output_key=preview_key,
            )
        )
        segment_debug.append(
            {
                "shot_id": shot_id,
                "engine": shot["backend_hint"],
                "model": model,
                "remote_video_url": remote_video_url,
                "output_key": preview_key,
                "raw_output": payload,
            }
        )

    stitched_output_key = output_key or f"stitched/{project_id}/commercial-nextgen.mp4"
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

    music_payload = openrouter.generate_music(
        prompt=build_music_prompt(plan["music_direction"], product_brief["product_category"]),
        duration_seconds=max(sum(int(shot["duration_sec"]) for shot in plan["shots"]), 18),
    )
    music_url = extract_audio_url(music_payload)
    music_path = get_runtime_path(settings, "music", project_id, "background.mp3")
    music_path.parent.mkdir(parents=True, exist_ok=True)
    download_remote_file(music_url, music_path)

    mixed_audio_path = mix_voice_and_music(
        video_path=stitched_path,
        voice_path=voiceover_path,
        music_path=music_path,
    )
    stitched_path = mixed_audio_path

    master_voiceover_key = f"audio/{project_id}/master-voiceover.mp3"
    background_music_key = f"audio/{project_id}/background-music.mp3"
    storage.upload_file(str(voiceover_path), master_voiceover_key)
    storage.upload_file(str(music_path), background_music_key)
    stitched_uri = storage.upload_file(str(stitched_path), stitched_output_key)

    save_hq_checkpoint(
        settings,
        project_id,
        {
            "pipeline": "nextgen",
            "product_brief": product_brief,
            "product_analysis": product_analysis,
            "plan": plan,
            "segments": [segment.model_dump() for segment in segments],
            "segment_debug": segment_debug,
            "master_voiceover_key": master_voiceover_key,
            "background_music_key": background_music_key,
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
        "background_music_key": background_music_key,
        "stitched_output_key": stitched_output_key,
        "stitched_output_uri": stitched_uri,
        "stitched_local_path": str(stitched_path),
    }


def normalize_nextgen_script(payload: dict) -> dict:
    base = normalize_hq_script(
        {
            "concept": payload.get("concept", ""),
            "voiceover_script": payload.get("voiceover_script", ""),
            "supers": payload.get("supers", []),
            "music_direction": payload.get("music_direction", ""),
            "shots": [
                {
                    "shot_id": shot.get("shot_id"),
                    "shot_type": normalize_nextgen_shot_type(index),
                    "duration_sec": shot.get("duration_sec", 4),
                    "appearance_prompt": shot.get("prompt", ""),
                    "motion_prompt": shot.get("motion", ""),
                    "camera_prompt": shot.get("camera", ""),
                    "voiceover_line": shot.get("voiceover_line", ""),
                    "on_screen_text": shot.get("on_screen_text", ""),
                }
                for index, shot in enumerate(payload.get("shots", []), start=1)
            ],
        }
    )
    for shot in base["shots"]:
        shot["backend_hint"] = "sora" if shot["shot_id"] in {"shot001", "shot005"} else "wan"
    return base


def normalize_nextgen_shot_type(index: int) -> str:
    if index in {1, 5}:
        return "talking_presenter"
    if index == 4:
        return "benefit_cutaway"
    return "hero_product"


def build_video_prompt(*, shot: dict, product_brief: dict, presenter_profile: dict) -> str:
    parts = [
        shot.get("appearance_prompt", ""),
        shot.get("motion_prompt", ""),
        shot.get("camera_prompt", ""),
        (
            f"Premium cinematic commercial for {product_brief['product_name']} in the {product_brief['product_category']} category. "
            f"Keep branding and packaging consistent. Presenter identity should match {presenter_profile['origin']}."
        ),
    ]
    return ". ".join(part for part in parts if part)


def build_music_prompt(music_direction: str, category: str) -> str:
    return (
        f"Create a premium instrumental advertising soundtrack for a {category} commercial. "
        f"{music_direction or 'Warm cinematic rhythm, polished premium feel, no vocals.'}"
    )


def mix_voice_and_music(*, video_path: Path, voice_path: Path, music_path: Path) -> Path:
    output_path = video_path.with_name(f"{video_path.stem}.mix{video_path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(voice_path),
        "-i",
        str(music_path),
        "-filter_complex",
        "[2:a]volume=0.18[music];[1:a][music]amix=inputs=2:duration=first:dropout_transition=2[mix]",
        "-map",
        "0:v:0",
        "-map",
        "[mix]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    return output_path

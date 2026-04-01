from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from text2video.aws.session import build_boto3_session
from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.config import get_runtime_path, get_settings
from text2video.runpod.client import RunpodInferenceClient
from text2video.runpod.schemas import LtxGenerateRequest
from text2video.worker.contracts import StitchSegmentRef, StitchWorkerPayload
from text2video.worker.stitch import run_ffmpeg_stitch


DEFAULT_PROMPT = (
    "Create a 20 second premium shampoo commercial with a confident man holding the shampoo bottle, "
    "presenting it to camera, rotating it slightly, smiling, and delivering spokesperson-style product moments. "
    "Keep the bottle branding, blue-white packaging, and Head & Shoulders look consistent with the reference image. "
    "Plan multiple short shots that can be stitched into one polished ad."
)

BEDROCK_COMMERCIAL_SYSTEM_PROMPT = """You are a commercial creative director and copywriter for an AI video generation pipeline.

Write a short premium product-ad script for a stitched multi-shot video commercial.

Goals:
- Sell the product clearly and quickly
- Keep the brand/product visually consistent with the provided reference image
- Write for image-to-video generation, not for a human film crew
- Make the output useful for shot planning, voiceover generation, and LTX prompts

Return valid JSON only with these keys:
- concept
- voiceover_script
- supers
- music_direction
- shots

Rules:
- The product is the hero in every shot
- The commercial should feel premium, clean, modern, and trustworthy
- Keep shots short and stitchable
- Include a male spokesperson holding the product in at least one shot
- Each shot must include:
  - shot_id
  - duration_sec
  - shot_type
  - appearance_prompt
  - motion_prompt
  - camera_prompt
  - voiceover_line
  - on_screen_text
- Keep the whole ad around 15 to 20 seconds
- Write natural commercial copy, not exaggerated hype
- No markdown
- No explanation outside JSON"""

BEDROCK_COMMERCIAL_USER_PROMPT = """Create a premium shampoo commercial for the attached product reference image.

Product:
Head & Shoulders Deep Scalp Cleanse shampoo

Creative direction:
- A confident man holds the shampoo bottle and presents it naturally
- Show clean premium studio lighting
- Include hero product closeups, label visibility, hand-held product shots, and polished beauty-commercial motion
- Keep the bottle branding, colors, and packaging consistent with the reference image
- Tone should feel modern, trustworthy, fresh, and premium
- The final output will be generated as multiple stitched AI video shots
- Optimize the script for LTX image-to-video generation
- Include short voiceover lines and on-screen text for each shot

Audience:
People looking for a premium anti-dandruff / scalp-care shampoo

Desired outcome:
- 4 to 6 shots
- around 15 to 20 seconds total
- strong opening hook
- clear product benefit
- polished closing brand moment"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan, generate, and stitch an LTX commercial.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--product-image-key", required=True)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-shots", type=int, default=4)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--num-inference-steps", type=int, default=8)
    parser.add_argument("--guidance-scale", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-key", default="")
    parser.add_argument("--voice-id", default="Matthew")
    parser.add_argument("--voice-engine", default="neural")
    parser.add_argument("--without-voiceover", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()

    if not settings.runpod_ltx_inference_base_url:
        raise SystemExit("RUNPOD_LTX_INFERENCE_BASE_URL is not configured")

    planner = ShotPlanner(settings)
    storage = S3Storage(settings)
    client = RunpodInferenceClient(settings, base_url=settings.runpod_ltx_inference_base_url)

    references = [
        {
            "type": "product_image",
            "s3_key": args.product_image_key,
            "note": "Maintain product identity and visible branding.",
        }
    ]
    commercial_brief = f"{BEDROCK_COMMERCIAL_USER_PROMPT}\n\nReference image key: {args.product_image_key}\n\nExtra direction: {args.prompt}"
    commercial_script = planner._parse_json_response(
        planner.client.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": settings.bedrock_temperature,
                "maxTokens": settings.bedrock_max_tokens,
            },
            system=[{"text": BEDROCK_COMMERCIAL_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": commercial_brief}]}],
        )["output"]["message"]["content"][0]["text"]
    )
    plan = normalize_commercial_script(commercial_script)
    shots = plan["shots"][: args.max_shots]

    outputs_root = get_runtime_path(settings, "outputs", args.project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)

    source_image_url = storage.create_presigned_download(args.product_image_key, expires_in=3600)["url"]
    generated_segments: list[StitchSegmentRef] = []

    for shot in shots:
        shot_id = shot["shot_id"]
        preview_key = f"previews/{args.project_id}/{shot_id}.mp4"
        num_frames = duration_to_num_frames(shot.get("duration_sec", 3))

        request = LtxGenerateRequest(
            project_id=args.project_id,
            shot_id=shot_id,
            prompt=compose_ltx_prompt(shot),
            source_image_key=args.product_image_key,
            source_image_url=source_image_url,
            output_key=preview_key,
            upload_url=storage.create_presigned_upload(preview_key, expires_in=3600)["url"],
            width=args.width,
            height=args.height,
            num_frames=num_frames,
            num_inference_steps=args.num_inference_steps,
            guidance_scale=args.guidance_scale,
            seed=args.seed,
        )
        response = client.generate_ltx_preview(request)

        local_segment_path = outputs_root / Path(response.s3_key).name
        storage.download_file(response.s3_key, str(local_segment_path))

        generated_segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"ltx-{shot_id}",
                job_type="generate_preview",
                backend_hint="ltx",
                duration_sec=shot.get("duration_sec", 3),
                transition="crossfade" if shot.get("shot_type") == "transition" else "hard_cut",
                output_key=response.s3_key,
            )
        )

    stitched_output_key = args.output_key or f"stitched/{args.project_id}/commercial.mp4"
    stitch_payload = StitchWorkerPayload(
        project_id=args.project_id,
        scene_id="scene001",
        manifest_sk=f"MANIFEST#{args.project_id}",
        output_key=stitched_output_key,
        continuity=plan["continuity"],
        segments=generated_segments,
    )
    stitched_path = run_ffmpeg_stitch(settings, stitch_payload)
    final_output_path = stitched_path
    final_output_key = stitched_output_key

    voiceover_audio_path = None
    voiced_output_key = ""
    voiced_output_uri = ""
    if not args.without_voiceover and plan["voiceover_script"].strip():
        voiceover_audio_path = synthesize_voiceover(
            settings=settings,
            project_id=args.project_id,
            script_text=plan["voiceover_script"],
            voice_id=args.voice_id,
            engine=args.voice_engine,
        )
        final_output_path = mux_voiceover_onto_video(
            video_path=stitched_path,
            voiceover_path=voiceover_audio_path,
            output_path=stitched_path.with_name(f"{stitched_path.stem}-vo.mp4"),
        )
        final_output_key = stitched_output_key.replace(".mp4", "-vo.mp4")
        voiced_output_key = final_output_key
        voiced_output_uri = storage.upload_file(str(final_output_path), final_output_key)

    stitched_uri = storage.upload_file(str(stitched_path), stitched_output_key)

    print(
        json.dumps(
            {
                "project_id": args.project_id,
                "summary": plan["summary"],
                "concept": plan["concept"],
                "voiceover_script": plan["voiceover_script"],
                "supers": plan["supers"],
                "music_direction": plan["music_direction"],
                "continuity": plan["continuity"],
                "shots": shots,
                "segments": [segment.model_dump() for segment in generated_segments],
                "stitched_output_key": stitched_output_key,
                "stitched_output_uri": stitched_uri,
                "stitched_local_path": str(stitched_path),
                "voiceover_script_path": str(voiceover_audio_path) if voiceover_audio_path else "",
                "voiced_output_key": voiced_output_key,
                "voiced_output_uri": voiced_output_uri,
                "final_output_key": final_output_key,
                "final_output_uri": voiced_output_uri or stitched_uri,
                "final_local_path": str(final_output_path),
            },
            indent=2,
        )
    )


def compose_ltx_prompt(shot: dict) -> str:
    parts = [
        shot.get("appearance_prompt", "").strip(),
        shot.get("motion_prompt", "").strip(),
        shot.get("camera_prompt", "").strip(),
        f"Voiceover timing reference: {shot.get('voiceover_line', '').strip()}".strip(),
        f"On-screen text: {shot.get('on_screen_text', '').strip()}".strip(),
    ]
    return ". ".join(part for part in parts if part)


def duration_to_num_frames(duration_sec: int) -> int:
    target_frames = max(17, int(duration_sec * 24))
    valid_frames = ((target_frames - 1 + 7) // 8) * 8 + 1
    return valid_frames


def synthesize_voiceover(
    settings,
    project_id: str,
    script_text: str,
    voice_id: str,
    engine: str,
) -> Path:
    polly = build_boto3_session(settings).client("polly", region_name=settings.aws_default_region)
    output_dir = get_runtime_path(settings, "voiceover", project_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "voiceover.mp3"

    ssml = build_voiceover_ssml(script_text)
    response = polly.synthesize_speech(
        Engine=engine,
        VoiceId=voice_id,
        OutputFormat="mp3",
        TextType="ssml",
        Text=ssml,
    )
    stream = response["AudioStream"]
    with output_path.open("wb") as file_handle:
        file_handle.write(stream.read())
    return output_path


def build_voiceover_ssml(script_text: str) -> str:
    cleaned = " ".join(script_text.split())
    sentences = [part.strip() for part in cleaned.split(".") if part.strip()]
    if not sentences:
        sentences = [cleaned]
    body = '<break time="500ms"/>'.join(f"{sentence}." for sentence in sentences)
    return f"<speak><prosody rate=\"95%\">{body}</prosody></speak>"


def mux_voiceover_onto_video(video_path: Path, voiceover_path: Path, output_path: Path) -> Path:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(voiceover_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
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


def normalize_commercial_script(payload: dict) -> dict:
    shots = []
    continuity = [
        "Keep Head & Shoulders bottle branding, blue-white packaging, and label visibility consistent.",
        "Maintain premium studio lighting and clean commercial styling across shots.",
        "Preserve the male spokesperson identity and product-holding hand continuity.",
    ]

    for index, shot in enumerate(payload.get("shots", []), start=1):
        shots.append(
            {
                "shot_id": str(shot.get("shot_id") or f"shot{index:03d}"),
                "sequence_index": index,
                "shot_type": str(shot.get("shot_type") or "medium"),
                "duration_sec": min(max(int(shot.get("duration_sec", 3)), 2), 6),
                "backend_hint": "ltx",
                "audio_mode": "speech_and_ambience",
                "appearance_prompt": str(shot.get("appearance_prompt") or "").strip(),
                "motion_prompt": str(shot.get("motion_prompt") or "").strip(),
                "camera_prompt": str(shot.get("camera_prompt") or "").strip(),
                "quality_tier": "preview",
                "voiceover_line": str(shot.get("voiceover_line") or "").strip(),
                "on_screen_text": str(shot.get("on_screen_text") or "").strip(),
            }
        )

    return {
        "summary": str(payload.get("concept") or "Premium shampoo commercial").strip(),
        "concept": str(payload.get("concept") or "").strip(),
        "voiceover_script": str(payload.get("voiceover_script") or "").strip(),
        "supers": payload.get("supers", []),
        "music_direction": str(payload.get("music_direction") or "").strip(),
        "continuity": continuity,
        "shots": shots,
    }


if __name__ == "__main__":
    main()

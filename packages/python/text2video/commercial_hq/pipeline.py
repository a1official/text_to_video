from __future__ import annotations

import json
import subprocess
import base64
from pathlib import Path
import httpx

from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.commercial_hq.public_endpoints import RunpodPublicEndpointClient
from text2video.commercial_hq.presenter_generator import generate_presenter_image
from text2video.commercial_hq.router import choose_shot_engine
from text2video.config import Settings, get_runtime_path
from text2video.worker.contracts import StitchSegmentRef, StitchWorkerPayload
from text2video.worker.stitch import run_ffmpeg_stitch


BEDROCK_HQ_SYSTEM_PROMPT = """You are a commercial creative director building a premium stitched AI advertisement.

Return valid JSON only with keys:
- concept
- voiceover_script
- supers
- music_direction
- shots

Rules:
- Build a premium stitched commercial that feels like one unified ad, not separate mini videos
- Use exactly 5 shots
- Use exactly 2 talking presenter shots: one opening presenter beat and one closing presenter beat
- Use exactly 3 non-speaking product or benefit shots between the presenter beats
- The five-shot narrative order must be:
  - opening presenter hook
  - hero product reveal
  - texture or usage detail
  - benefit or lifestyle product payoff
  - closing presenter or packshot close
- Use the structured product brief as the source of truth for product facts, claims, audience, and tone
- Each shot must include:
  - shot_id
  - duration_sec
  - shot_type
  - appearance_prompt
  - motion_prompt
  - camera_prompt
  - voiceover_line
  - on_screen_text
- Use shot_type values from:
  - hero_product
  - talking_presenter
  - benefit_cutaway
  - endcard
- Keep product branding and packaging consistent with the reference image
- Write natural English ad copy, not hype
- Do not invent unsupported product claims beyond the supplied brief
- Make every spoken line advance the story; avoid repeating the same claim in multiple shots
- The full voiceover_script must read as one continuous commercial narration
- No markdown
- No explanation outside JSON"""


def run_hq_commercial(
    *,
    settings: Settings,
    project_id: str,
    product_image_key: str,
    presenter_image_key: str = "",
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
    max_shots: int,
    width: int,
    height: int,
    num_inference_steps: int,
    guidance_scale: float,
    seed: int,
    output_key: str,
    voice_id: str,
    voice_engine: str,
) -> dict:
    planner = ShotPlanner(settings)
    storage = S3Storage(settings)
    public_client = RunpodPublicEndpointClient(settings)
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

    commercial_brief = build_bedrock_hq_brief(
        product_brief=product_brief,
        product_image_key=product_image_key,
        presenter_image_key=presenter_image_key,
    )
    commercial_script = planner._parse_json_response(
        planner.client.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": settings.bedrock_temperature,
                "maxTokens": settings.bedrock_max_tokens,
            },
            system=[{"text": BEDROCK_HQ_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": commercial_brief}]}],
        )["output"]["message"]["content"][0]["text"]
    )
    plan = normalize_hq_script(commercial_script)
    shots = plan["shots"][:max_shots]

    outputs_root = get_runtime_path(settings, "outputs", project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)

    product_image_url = storage.create_presigned_download(product_image_key, expires_in=3600)["url"]

    generated_segments: list[StitchSegmentRef] = []
    segment_debug: list[dict] = []
    presenter_cache: dict[str, dict] = {}

    for shot in shots:
        shot_id = shot["shot_id"]
        engine = choose_shot_engine(shot)

        if engine == "infinitetalk":
            if presenter_image_key:
                presenter_image_url = storage.create_presigned_download(presenter_image_key, expires_in=3600)["url"]
                presenter_debug = {
                    "source": "provided_presenter_image",
                    "s3_key": presenter_image_key,
                }
            else:
                presenter_cache_key = presenter_profile["gender"]
                presenter_asset = presenter_cache.get(presenter_cache_key)
                if presenter_asset is None:
                    presenter_asset = generate_presenter_image(
                        settings=settings,
                        project_id=project_id,
                        shot_id=f"{presenter_cache_key}-presenter",
                        prompt=compose_presenter_prompt(shot, product_brief, presenter_profile),
                        product_image_key=product_image_key,
                    )
                    presenter_cache[presenter_cache_key] = presenter_asset
                presenter_image_url = presenter_asset["download_url"]
                presenter_debug = {
                    "source": "reused_nano_banana_presenter_generator",
                    "s3_key": presenter_asset["s3_key"],
                    "cache_key": presenter_cache_key,
                }

            audio_path = synthesize_voice_line(
                settings=settings,
                project_id=project_id,
                shot_id=shot_id,
                script_text=shot["voiceover_line"] or plan["voiceover_script"],
                voice_id=voice_id,
                engine=voice_engine,
            )
            audio_key = f"audio/{project_id}/{shot_id}.mp3"
            storage.upload_file(str(audio_path), audio_key)
            audio_url = storage.create_presigned_download(audio_key, expires_in=3600)["url"]

            sync_payload = public_client.generate_infinitetalk(
                prompt=compose_infinitetalk_prompt(shot, presenter_profile),
                image_url=presenter_image_url,
                audio_url=audio_url,
                resolution="480p",
            )
            talking_video_url = extract_public_video_url(sync_payload)

            local_segment_path = outputs_root / f"{shot_id}.mp4"
            download_remote_file(talking_video_url, local_segment_path)
            preview_key = f"previews/{project_id}/{shot_id}.mp4"
            storage.upload_file(str(local_segment_path), preview_key)

            generated_segments.append(
                StitchSegmentRef(
                    shot_id=shot_id,
                    sequence_index=shot.get("sequence_index"),
                    job_id=f"infinitetalk-{shot_id}",
                    job_type="generate_preview",
                    backend_hint="infinitetalk",
                    duration_sec=shot.get("duration_sec", 4),
                    transition="hard_cut",
                    output_key=preview_key,
                )
            )
            segment_debug.append(
                {
                    "shot_id": shot_id,
                    "engine": engine,
                    "presenter_asset": presenter_debug,
                    "audio_key": audio_key,
                    "remote_video_url": talking_video_url,
                    "cost": sync_payload["output"].get("cost"),
                    "output_key": preview_key,
                }
            )
            continue

        preview_key = f"previews/{project_id}/{shot_id}.mp4"
        seedance_payload = public_client.generate_seedance_i2v(
            prompt=compose_seedance_prompt(shot, product_brief),
            image_url=product_image_url,
            duration=min(max(int(shot.get("duration_sec", 4)), 4), 4),
            resolution="480p",
            aspect_ratio="16:9",
            seed=seed,
        )
        video_url = extract_public_video_url(seedance_payload)
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        download_remote_file(video_url, local_segment_path)
        storage.upload_file(str(local_segment_path), preview_key)
        generated_segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"seedance-{shot_id}",
                job_type="generate_preview",
                backend_hint="seedance",
                duration_sec=shot.get("duration_sec", 4),
                transition="hard_cut",
                output_key=preview_key,
            )
        )
        segment_debug.append(
            {
                "shot_id": shot_id,
                "engine": engine,
                "remote_video_url": video_url,
                "cost": seedance_payload["output"].get("cost"),
                "output_key": preview_key,
                "raw_output": seedance_payload["output"],
            }
        )

    stitched_output_key = output_key or f"stitched/{project_id}/commercial-hq.mp4"
    stitch_payload = StitchWorkerPayload(
        project_id=project_id,
        scene_id="scene001",
        manifest_sk=f"MANIFEST#{project_id}",
        output_key=stitched_output_key,
        continuity=plan["continuity"],
        segments=generated_segments,
    )
    stitched_path = run_ffmpeg_stitch(settings, stitch_payload)
    master_voiceover_path = synthesize_voice_line(
        settings=settings,
        project_id=project_id,
        shot_id="master-voiceover",
        script_text=plan["voiceover_script"],
        voice_id=voice_id,
        engine=voice_engine,
    )
    stitched_path = mux_master_voiceover(
        video_path=stitched_path,
        audio_path=master_voiceover_path,
    )
    master_voiceover_key = f"audio/{project_id}/master-voiceover.mp3"
    storage.upload_file(str(master_voiceover_path), master_voiceover_key)
    stitched_uri = storage.upload_file(str(stitched_path), stitched_output_key)

    return {
        "project_id": project_id,
        "summary": plan["summary"],
        "concept": plan["concept"],
        "voiceover_script": plan["voiceover_script"],
        "supers": plan["supers"],
        "music_direction": plan["music_direction"],
        "shots": shots,
        "segments": [segment.model_dump() for segment in generated_segments],
        "segment_debug": segment_debug,
        "master_voiceover_key": master_voiceover_key,
        "stitched_output_key": stitched_output_key,
        "stitched_output_uri": stitched_uri,
        "stitched_local_path": str(stitched_path),
    }


def normalize_product_brief(
    *,
    brief_mode: str,
    product_name: str,
    product_category: str,
    product_description: str,
    target_audience: str,
    key_benefits: list[str],
    brand_tone: str,
    call_to_action: str,
    additional_notes: str,
    extra_direction: str,
    presenter_profile: dict,
) -> dict:
    normalized_name = (product_name or "").strip() or "Premium product"
    normalized_category = (product_category or "").strip() or "consumer packaged good"
    cleaned_benefits = [benefit.strip() for benefit in key_benefits if benefit.strip()]

    return {
        "brief_mode": (brief_mode or "quick").strip().lower(),
        "product_name": normalized_name,
        "product_category": normalized_category,
        "product_description": (product_description or "").strip(),
        "target_audience": (target_audience or "").strip(),
        "key_benefits": cleaned_benefits,
        "brand_tone": (brand_tone or "").strip() or "Premium, trustworthy, English-language commercial",
        "call_to_action": (call_to_action or "").strip(),
        "additional_notes": (additional_notes or "").strip(),
        "extra_direction": (extra_direction or "").strip(),
        "presenter_profile": presenter_profile,
    }


def build_bedrock_hq_brief(*, product_brief: dict, product_image_key: str, presenter_image_key: str) -> str:
    benefits = product_brief["key_benefits"] or ["Use only visually supported or user-supplied benefits."]
    presenter_profile = product_brief["presenter_profile"]
    lines = [
        "Create a premium English commercial using the structured product brief below.",
        "",
        "Product brief:",
        f"- Brief mode: {product_brief['brief_mode']}",
        f"- Product name: {product_brief['product_name']}",
        f"- Product category: {product_brief['product_category']}",
        f"- Product description: {product_brief['product_description'] or 'Not supplied'}",
        f"- Target audience: {product_brief['target_audience'] or 'General audience; infer only from category and tone'}",
        f"- Key benefits: {', '.join(benefits)}",
        f"- Brand tone: {product_brief['brand_tone']}",
        f"- Call to action: {product_brief['call_to_action'] or 'Not supplied'}",
        f"- Additional notes: {product_brief['additional_notes'] or 'None'}",
        f"- Extra creative direction: {product_brief['extra_direction'] or 'Keep it premium and concise.'}",
        f"- Presenter identity: {presenter_profile['label']}",
        f"- Presenter styling: {presenter_profile['styling']}",
        "",
        "Visual assets:",
        f"- Product image key: {product_image_key}",
        f"- Presenter image key: {presenter_image_key or 'Not supplied; presenter still will be generated separately'}",
        "",
        "Creative requirements:",
        "- Speaking shots should be written to work as lip-synced presenter moments.",
        "- Product shots should feel glossy, cinematic, clean, premium, and trustworthy.",
        "- Keep the bottle branding, colors, and packaging consistent with the reference image.",
        "- Build a better-quality stitched ad using different shot engines for presenter and product moments.",
        "- Include short voiceover lines and on-screen text for each shot.",
        f"- The presenter must match this identity: {presenter_profile['label']}.",
    ]
    return "\n".join(lines)


def normalize_hq_script(payload: dict) -> dict:
    shots = []
    continuity = [
        "Keep the uploaded product packshot consistent across every generated shot.",
        "Maintain the same presenter identity across all talking shots.",
        "Preserve a premium studio-commercial look with clean lighting and polished skin tones.",
    ]
    shot_type_aliases = {
        "talking_head": "talking_presenter",
        "closeup": "hero_product",
        "insert": "hero_product",
        "transition": "benefit_cutaway",
    }

    for index, shot in enumerate(payload.get("shots", []), start=1):
        shot_type = str(shot.get("shot_type") or "hero_product").strip().lower().replace("-", "_")
        shot_type = shot_type_aliases.get(shot_type, shot_type)
        if shot_type not in {"hero_product", "talking_presenter", "benefit_cutaway", "endcard"}:
            shot_type = "hero_product"
        shots.append(
            {
                "shot_id": str(shot.get("shot_id") or f"shot{index:03d}"),
                "sequence_index": index,
                "shot_type": shot_type,
                "duration_sec": min(max(int(shot.get("duration_sec", 3)), 2), 8),
                "audio_mode": "speech" if shot_type == "talking_presenter" else "ambience",
                "appearance_prompt": str(shot.get("appearance_prompt") or "").strip(),
                "motion_prompt": str(shot.get("motion_prompt") or "").strip(),
                "camera_prompt": str(shot.get("camera_prompt") or "").strip(),
                "voiceover_line": str(shot.get("voiceover_line") or "").strip(),
                "on_screen_text": str(shot.get("on_screen_text") or "").strip(),
            }
        )

    shots = enforce_commercial_arc(shots)

    return {
        "summary": str(payload.get("concept") or "Premium mixed-engine commercial").strip(),
        "concept": str(payload.get("concept") or "").strip(),
        "voiceover_script": str(payload.get("voiceover_script") or "").strip(),
        "supers": payload.get("supers", []),
        "music_direction": str(payload.get("music_direction") or "").strip(),
        "continuity": continuity,
        "shots": shots,
    }


def enforce_commercial_arc(shots: list[dict]) -> list[dict]:
    if not shots:
        return []

    talking = [shot for shot in shots if shot["shot_type"] == "talking_presenter"]
    non_talking = [shot for shot in shots if shot["shot_type"] != "talking_presenter"]

    if not talking:
        opening = clone_shot_with_role(shots[0], "talking_presenter", 1, "opening presenter hook")
        closing = clone_shot_with_role(shots[min(len(shots) - 1, 0)], "talking_presenter", 5, "closing presenter close")
        talking = [opening, closing]
    elif len(talking) == 1:
        talking = [talking[0], clone_shot_with_role(talking[0], "talking_presenter", 5, "closing presenter close")]

    while len(non_talking) < 3:
        seed = non_talking[-1] if non_talking else shots[0]
        cloned = clone_shot_with_role(seed, "hero_product", len(non_talking) + 2, "product bridge")
        non_talking.append(cloned)

    middle = non_talking[:3]
    middle_roles = ["hero product reveal", "texture or usage detail", "benefit or payoff shot"]
    middle_types = ["hero_product", "hero_product", "benefit_cutaway"]

    arc = [
        clone_shot_with_role(talking[0], "talking_presenter", 1, "opening presenter hook"),
        clone_shot_with_role(middle[0], middle_types[0], 2, middle_roles[0]),
        clone_shot_with_role(middle[1], middle_types[1], 3, middle_roles[1]),
        clone_shot_with_role(middle[2], middle_types[2], 4, middle_roles[2]),
        clone_shot_with_role(talking[-1], "talking_presenter", 5, "closing presenter close"),
    ]

    for shot in arc:
        shot["duration_sec"] = 3 if shot["shot_type"] == "talking_presenter" else 4
    return arc


def clone_shot_with_role(source: dict, shot_type: str, sequence_index: int, role_hint: str) -> dict:
    cloned = dict(source)
    cloned["shot_id"] = f"shot{sequence_index:03d}"
    cloned["sequence_index"] = sequence_index
    cloned["shot_type"] = shot_type
    cloned["audio_mode"] = "speech" if shot_type == "talking_presenter" else "ambience"
    cloned["role_hint"] = role_hint
    return cloned


def compose_seedance_prompt(shot: dict, product_brief: dict) -> str:
    shot_type = str(shot.get("shot_type") or "").strip().lower()
    safe_overlay = build_safe_seedance_overlay(shot=shot, product_brief=product_brief)
    safe_appearance = sanitize_seedance_text(shot.get("appearance_prompt", "").strip())
    safe_motion = sanitize_seedance_text(shot.get("motion_prompt", "").strip())
    safe_camera = sanitize_seedance_text(shot.get("camera_prompt", "").strip())

    if shot_type == "benefit_cutaway":
        safe_appearance = (
            f"Premium beauty-commercial atmosphere around {product_brief['product_name']}, "
            "with elegant reflections, fresh surfaces, and polished studio styling"
        )
        safe_motion = "Subtle cinematic movement with a refined premium finish"
        safe_camera = "Close, polished product detail framing"

    parts = [
        safe_appearance,
        safe_motion,
        safe_camera,
        safe_overlay,
        (
            f"Luxury {product_brief['product_category']} commercial product shot for "
            f"{product_brief['product_name']} with premium motion, stable product identity, "
            "and precise label visibility."
        ),
    ]
    return ". ".join(part for part in parts if part)


def compose_infinitetalk_prompt(shot: dict, presenter_profile: dict) -> str:
    parts = [
        shot.get("appearance_prompt", "").strip(),
        shot.get("motion_prompt", "").strip(),
        shot.get("camera_prompt", "").strip(),
        (
            f"Natural {presenter_profile['label']} performance, premium commercial confidence, "
            "clear lip sync, product held visibly in hand, polished spokesperson energy."
        ),
    ]
    return ". ".join(part for part in parts if part)


def compose_presenter_prompt(shot: dict, product_brief: dict, presenter_profile: dict) -> str:
    benefits = ", ".join(product_brief["key_benefits"][:3])
    parts = [
        f"Create an English {product_brief['product_category']} commercial presenter still.",
        (
            f"A confident {presenter_profile['descriptor']} in a premium studio portrait holds a visible "
            f"{product_brief['product_name']} product naturally in one hand."
        ),
        (
            f"{presenter_profile['styling']}. Premium beauty-commercial lighting, "
            "realistic hands, realistic product hold."
        ),
        "Front-facing medium closeup, luxury advertising photography, English-market commercial style.",
        f"Imply these benefits visually without adding text: {benefits}." if benefits else "",
        shot.get("appearance_prompt", "").strip(),
        shot.get("camera_prompt", "").strip(),
    ]
    return ". ".join(part for part in parts if part)


def build_presenter_profile(voice_id: str) -> dict:
    female_voices = {"Joanna", "Amy", "Ruth", "Salli", "Kajal"}
    if voice_id in female_voices:
        return {
            "gender": "female",
            "label": "female presenter",
            "descriptor": "elegant confident woman",
            "styling": "Refined beauty styling, expressive eyes, camera-ready hair, assured smile",
        }
    return {
        "gender": "male",
        "label": "male presenter",
        "descriptor": "handsome confident man",
        "styling": "Clean grooming, camera-ready hair, assured smile, trustworthy spokesperson presence",
    }


def sanitize_seedance_text(value: str) -> str:
    replacements = {
        "scalp": "premium haircare",
        "healthy": "refined",
        "dandruff": "freshness",
        "anti-dandruff": "premium care",
        "ultimate": "premium",
        "trusted results": "premium finish",
        "cleaner, healthier": "fresh, refined",
    }
    sanitized = value
    for source, target in replacements.items():
        sanitized = sanitized.replace(source, target)
        sanitized = sanitized.replace(source.title(), target.title())
        sanitized = sanitized.replace(source.upper(), target.upper())
    return sanitized


def build_safe_seedance_overlay(*, shot: dict, product_brief: dict) -> str:
    shot_type = str(shot.get("shot_type") or "").strip().lower()
    if shot_type == "endcard":
        return f"On-screen text: {product_brief['product_name']}"
    if shot_type == "hero_product":
        return f"On-screen text: {product_brief['product_name']}"
    return ""


def synthesize_voice_line(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    script_text: str,
    voice_id: str,
    engine: str,
) -> Path:
    output_dir = get_runtime_path(settings, "voiceover", project_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{shot_id}.mp3"
    synthesize_sarvam_tts_mp3(
        settings=settings,
        script_text=script_text,
        voice_id=voice_id,
        target_path=output_path,
    )
    return output_path


def synthesize_sarvam_tts_mp3(
    *,
    settings: Settings,
    script_text: str,
    voice_id: str,
    target_path: Path,
) -> Path:
    if not settings.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured.")

    cleaned = " ".join(script_text.split()) or "Premium product commercial."
    response = httpx.post(
        settings.sarvam_tts_base_url,
        headers={
            "api-subscription-key": settings.sarvam_api_key,
            "Content-Type": "application/json",
        },
        json={
            "text": cleaned,
            "target_language_code": "en-IN",
            "speaker": sarvam_speaker_for_voice(voice_id),
            "model": "bulbul:v3",
            "pace": 1.0,
            "speech_sample_rate": 24000,
            "output_audio_codec": "mp3",
            "temperature": 0.4,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    audios = payload.get("audios") or []
    if not audios:
        raise RuntimeError(f"Sarvam TTS did not return audio: {payload}")
    target_path.write_bytes(base64.b64decode(audios[0]))
    return target_path


def sarvam_speaker_for_voice(voice_id: str) -> str:
    mapping = {
        "Matthew": "aditya",
        "Brian": "anand",
        "Joanna": "priya",
        "Amy": "amelia",
    }
    return mapping.get(voice_id, "aditya")

def mux_master_voiceover(*, video_path: Path, audio_path: Path) -> Path:
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
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    return output_path


def download_remote_file(url: str, target_path: Path) -> Path:
    with httpx.stream("GET", url, timeout=None) as response:
        response.raise_for_status()
        with target_path.open("wb") as file_handle:
            for chunk in response.iter_bytes():
                file_handle.write(chunk)
    return target_path


def extract_public_video_url(payload: dict) -> str:
    output = payload.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str) and item.startswith("http"):
                return item
            if isinstance(item, dict):
                for key in ("video_url", "url", "video", "file_url"):
                    value = item.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
    if isinstance(output, dict):
        for key in ("video_url", "url", "video", "file_url", "result"):
            value = output.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        media = output.get("media")
        if isinstance(media, list):
            for item in media:
                if isinstance(item, dict):
                    value = item.get("url")
                    if isinstance(value, str) and value.startswith("http"):
                        return value
    raise KeyError(f"Could not find video URL in payload output: {output}")

from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import time
from pathlib import Path

import httpx

from text2video.aws.s3 import S3Storage
from text2video.bedrock.planner import ShotPlanner
from text2video.commercial_hq.presenter_generator import generate_presenter_image
from text2video.commercial_hq.public_endpoints import RunpodPublicEndpointClient
from text2video.commercial_hq.product_understanding import (
    analyze_product_image,
    enrich_product_brief,
)
from text2video.commercial_hq.router import choose_shot_engine
from text2video.config import Settings, get_runtime_path
from text2video.tts.sarvam import synthesize_sarvam_tts_mp3
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
- Build a premium stitched story video that feels like one unified cinematic sequence, not a presenter-led ad
- Use a small set of visually rich shots
- Do not create any presenter or talking-head shots
- Focus on image-led storytelling, product reveal, motion, and atmospheric payoff
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
  - benefit_cutaway
  - endcard
-  - establishing
-  - wide
-  - medium
-  - closeup
-  - transition
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
    checkpoint = load_hq_checkpoint(settings, project_id)

    product_brief = checkpoint.get("product_brief")
    product_analysis = checkpoint.get("product_analysis")
    if not product_brief:
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
    if not product_analysis:
        product_analysis = analyze_product_image(
            settings=settings,
            project_id=project_id,
            product_image_key=product_image_key,
            user_product_name=product_brief["product_name"],
            user_product_category=product_brief["product_category"],
        )
        product_brief = enrich_product_brief(product_brief, product_analysis)
        checkpoint["product_brief"] = product_brief
        checkpoint["product_analysis"] = product_analysis
        save_hq_checkpoint(settings, project_id, checkpoint)
    else:
        product_brief = enrich_product_brief(product_brief, product_analysis)

    plan = checkpoint.get("plan")
    if not plan:
        commercial_brief = build_bedrock_hq_brief(
            product_brief=product_brief,
            product_image_key=product_image_key,
            presenter_image_key=presenter_image_key,
        )
        commercial_script = build_hq_commercial_script(
            settings=settings,
            planner=planner,
            commercial_brief=commercial_brief,
            product_brief=product_brief,
        )
        plan = normalize_hq_script(commercial_script)
        checkpoint["plan"] = plan
        save_hq_checkpoint(settings, project_id, checkpoint)
    shots = plan["shots"][:max_shots]
    checkpoint["shots"] = shots
    save_hq_checkpoint(settings, project_id, checkpoint)

    outputs_root = get_runtime_path(settings, "outputs", project_id)
    outputs_root.mkdir(parents=True, exist_ok=True)

    product_image_data_uri = data_uri_from_storage_key(
        settings=settings,
        storage=storage,
        project_id=project_id,
        key=product_image_key,
        cache_dir="source-assets",
    )

    generated_segments: list[StitchSegmentRef] = [
        StitchSegmentRef(**segment) for segment in checkpoint.get("segments", [])
    ]
    segment_debug: list[dict] = list(checkpoint.get("segment_debug", []))
    presenter_cache: dict[str, dict] = dict(checkpoint.get("presenter_cache", {}))
    if not generated_segments:
        generated_segments = recover_existing_segments(project_id=project_id, shots=shots, outputs_root=outputs_root)
        if generated_segments:
            checkpoint["segments"] = [segment.model_dump() for segment in generated_segments]
            save_hq_checkpoint(settings, project_id, checkpoint)
    completed_shot_ids = {segment.shot_id for segment in generated_segments}

    for shot in shots:
        shot_id = shot["shot_id"]
        if shot_id in completed_shot_ids:
            continue
        engine = choose_shot_engine(shot)

        if engine == "infinitetalk":
            if presenter_image_key:
                presenter_image_url = data_uri_from_storage_key(
                    settings=settings,
                    storage=storage,
                    project_id=project_id,
                    key=presenter_image_key,
                    cache_dir="presenters",
                )
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
                    checkpoint["presenter_cache"] = presenter_cache
                    save_hq_checkpoint(settings, project_id, checkpoint)
                presenter_image_url = presenter_asset["data_uri"]
                presenter_debug = {
                    "source": "runpod_nano_banana_2_edit_1k",
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
            audio_url = file_to_data_uri(audio_path)

            sync_payload = public_client.generate_infinitetalk(
                prompt=compose_infinitetalk_prompt(shot, presenter_profile),
                image_url=presenter_image_url,
                audio_url=audio_url,
                resolution="480p",
            )
            talking_video_url = extract_public_video_url(sync_payload)

            local_segment_path = outputs_root / f"{shot_id}.mp4"
            download_remote_file(talking_video_url, local_segment_path)
            normalize_segment_video(source_path=local_segment_path, width=width, height=height)
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
            checkpoint["segments"] = [segment.model_dump() for segment in generated_segments]
            checkpoint["segment_debug"] = segment_debug
            save_hq_checkpoint(settings, project_id, checkpoint)
            continue

        shot_image_key = str(shot.get("image_key") or "").strip()
        shot_image_data_uri = (
            data_uri_from_storage_key(
                settings=settings,
                storage=storage,
                project_id=project_id,
                key=shot_image_key,
                cache_dir="source-assets",
            )
            if shot_image_key
            else product_image_data_uri
        )
        preview_key = f"previews/{project_id}/{shot_id}.mp4"
        if engine == "veo":
            veo_payload = public_client.generate_veo_i2v(
                prompt=compose_veo_prompt(shot, product_brief),
                image_url=shot_image_data_uri,
                duration=min(max(int(shot.get("duration_sec", 4)), 4), 8),
                resolution="720p",
                aspect_ratio="16:9",
                generate_audio=False,
            )
            video_url = extract_public_video_url(veo_payload)
            raw_payload = veo_payload
            job_prefix = "veo"
        else:
            seedance_payload = public_client.generate_seedance_i2v(
                prompt=compose_seedance_prompt(shot, product_brief),
                image_url=shot_image_data_uri,
                duration=min(max(int(shot.get("duration_sec", 4)), 4), 4),
                resolution="480p",
                aspect_ratio="16:9",
                seed=seed,
            )
            video_url = extract_public_video_url(seedance_payload)
            raw_payload = seedance_payload
            job_prefix = "seedance"
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        download_remote_file(video_url, local_segment_path)
        normalize_segment_video(source_path=local_segment_path, width=width, height=height)
        storage.upload_file(str(local_segment_path), preview_key)
        generated_segments.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"{job_prefix}-{shot_id}",
                job_type="generate_preview",
                backend_hint=engine,
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
                "cost": raw_payload["output"].get("cost"),
                "output_key": preview_key,
                "raw_output": raw_payload["output"],
                "image_key": shot_image_key or product_image_key,
            }
        )
        checkpoint["segments"] = [segment.model_dump() for segment in generated_segments]
        checkpoint["segment_debug"] = segment_debug
        save_hq_checkpoint(settings, project_id, checkpoint)

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
    checkpoint["master_voiceover_key"] = master_voiceover_key
    checkpoint["stitched_output_key"] = stitched_output_key
    checkpoint["stitched_output_uri"] = stitched_uri
    checkpoint["stitched_local_path"] = str(stitched_path)
    save_hq_checkpoint(settings, project_id, checkpoint)

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
    product_analysis = product_brief.get("product_analysis", {})
    visible_pack_text = product_analysis.get("visible_pack_text", [])
    visible_claims = product_analysis.get("visible_claims", [])
    packaging_colors = product_analysis.get("packaging_colors", [])
    audience_hints = product_analysis.get("audience_hints", [])
    warnings = product_analysis.get("warnings", [])
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
        "",
        "Extracted image understanding:",
        f"- Detected brand: {product_analysis.get('detected_brand') or 'Unknown'}",
        f"- Detected product name: {product_analysis.get('detected_product_name') or 'Unknown'}",
        f"- Detected category: {product_analysis.get('detected_category') or 'Unknown'}",
        f"- Packaging colors: {', '.join(packaging_colors) or 'Unknown'}",
        f"- Visible pack text: {', '.join(visible_pack_text) or 'Unknown'}",
        f"- Visible claims: {', '.join(visible_claims) or 'Unknown'}",
        f"- Visual summary: {product_analysis.get('visual_summary') or 'Not available'}",
        f"- Audience hints: {', '.join(audience_hints) or 'None'}",
        f"- Image analysis warnings: {', '.join(warnings) or 'None'}",
        "",
        "Visual assets:",
        f"- Product image key: {product_image_key}",
        f"- Presenter image key: {presenter_image_key or 'Not supplied; do not create a presenter for this story video'}",
        "",
        "Creative requirements:",
        "- Do not create any presenter or lip-synced talking-head shots.",
        "- Product shots should feel glossy, cinematic, clean, premium, and trustworthy.",
        "- Keep the bottle branding, colors, and packaging consistent with the reference image.",
        "- Build a better-quality stitched story video using image-led motion and cinematic transitions.",
        "- Include short voiceover lines and on-screen text for each shot.",
    ]
    return "\n".join(lines)


def build_hq_commercial_script(
    *,
    settings: Settings,
    planner: ShotPlanner,
    commercial_brief: str,
    product_brief: dict,
) -> dict:
    try:
        return planner._parse_json_response(
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
    except Exception:
        if settings.openrouter_api_key:
            try:
                return openrouter_chat_json(
                    settings=settings,
                    model="openai/gpt-4.1-mini",
                    system_prompt=BEDROCK_HQ_SYSTEM_PROMPT,
                    user_payload={"brief": commercial_brief},
                )
            except Exception:
                pass
        return build_checkpoint_fallback_script(product_brief)


def openrouter_chat_json(*, settings: Settings, model: str, system_prompt: str, user_payload: dict) -> dict:
    response = httpx.post(
        f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000/openrouter",
            "X-Title": "Mercury Studio HQ Fallback",
        },
        json={
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2)},
            ],
        },
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    return json.loads(payload["choices"][0]["message"]["content"])


def build_checkpoint_fallback_script(product_brief: dict) -> dict:
    product_name = product_brief.get("product_name", "Premium product")
    category = product_brief.get("product_category", "digital product")
    benefits = product_brief.get("key_benefits", []) or []
    call_to_action = product_brief.get("call_to_action", "").strip() or f"Plan smarter with {product_name}."
    extra_direction = product_brief.get("additional_notes", "")

    benefit_lines = benefits[:3] or [
        "Create complete itineraries in minutes",
        "Real stays, food spots, and experiences",
        "Built from real traveler stories",
    ]
    voiceover_script = (
        "Stop planning trips from scratch. "
        f"With {product_name}, create, customize, and share complete travel itineraries in minutes. "
        f"{benefit_lines[0]}. {benefit_lines[1]}. {benefit_lines[2]}. "
        f"{call_to_action}"
    )
    return {
        "concept": f"From planning chaos to calm clarity with {product_name}",
        "voiceover_script": voiceover_script,
        "supers": [
            "Stop planning trips from scratch.",
            benefit_lines[0],
            benefit_lines[1],
            benefit_lines[2],
            call_to_action,
        ],
        "music_direction": "Modern travel-tech build with light momentum, crisp percussion, and optimistic lift.",
        "shots": [
            {
                "shot_id": "shot001",
                "duration_sec": 3,
                "shot_type": "hero_product",
                "appearance_prompt": (
                    f"Split-screen {category} story video. Left side shows a frustrated traveler with too many browser tabs, "
                    f"right side introduces {product_name} interface with clean itinerary cards and polished travel-tech styling."
                ),
                "motion_prompt": "The right side begins organizing the chaos into a clear plan without any presenter or talking-head beat.",
                "camera_prompt": "Balanced split-screen composition with premium cinematic motion and no presenter framing.",
                "voiceover_line": "Stop planning trips from scratch.",
                "on_screen_text": "Stop planning trips from scratch.",
            },
            {
                "shot_id": "shot002",
                "duration_sec": 4,
                "shot_type": "hero_product",
                "appearance_prompt": (
                    f"Elegant product UI reveal for {product_name}, showing a beautiful itinerary with destination cards, day-by-day structure, "
                    "and premium travel app design."
                ),
                "motion_prompt": "The itinerary assembles instantly from scattered planning fragments into one beautiful, structured trip plan.",
                "camera_prompt": "Clean cinematic push-in on the app interface with crisp typography and refined glassy UI details.",
                "voiceover_line": f"With {product_name}, create, customize, and share complete travel itineraries in minutes.",
                "on_screen_text": "Create, customize, and share complete itineraries in minutes",
            },
            {
                "shot_id": "shot003",
                "duration_sec": 4,
                "shot_type": "hero_product",
                "appearance_prompt": "Premium itinerary detail shot featuring stays, food spots, and experiences arranged in polished travel cards.",
                "motion_prompt": "Cards animate smoothly into place, highlighting real stays, real food spots, and real experiences.",
                "camera_prompt": "Close product-detail framing with layered UI parallax and premium travel editorial styling.",
                "voiceover_line": f"{benefit_lines[1]}.",
                "on_screen_text": benefit_lines[1],
            },
            {
                "shot_id": "shot004",
                "duration_sec": 4,
                "shot_type": "benefit_cutaway",
                "appearance_prompt": (
                    f"Lifestyle travel planning payoff around {product_name}, with organized route cards, shared itinerary moments, "
                    "and a feeling of calm control replacing confusion."
                ),
                "motion_prompt": "User effortlessly customizes and shares the itinerary while the interface feels intuitive and calm.",
                "camera_prompt": "Smooth close-to-medium framing with premium motion graphics and a reassuring commercial rhythm.",
                "voiceover_line": f"{benefit_lines[2]}.",
                "on_screen_text": benefit_lines[2],
            },
            {
                "shot_id": "shot005",
                "duration_sec": 3,
                "shot_type": "endcard",
                "appearance_prompt": (
                    f"Polished end-card for {product_name} with the product visible beside a finished itinerary "
                    "and a clean cinematic closing frame."
                ),
                "motion_prompt": "The itinerary locks into a beautiful finished state and the closing frame holds without a presenter.",
                "camera_prompt": "Premium end-card composition with subtle product UI background and polished closing motion.",
                "voiceover_line": call_to_action,
                "on_screen_text": call_to_action,
            },
        ],
        "fallback_notes": extra_direction,
    }


def normalize_hq_script(payload: dict) -> dict:
    shots = []
    continuity = [
        "Keep the uploaded product packshot consistent across every generated shot.",
        "Preserve a premium studio-commercial look with clean lighting and polished skin tones.",
        "Avoid any presenter or talking-head moments; keep the sequence image-led.",
    ]
    shot_type_aliases = {
        "talking_head": "hero_product",
        "talking_presenter": "hero_product",
        "closeup": "hero_product",
        "insert": "hero_product",
        "transition": "benefit_cutaway",
        "presenter": "hero_product",
    }

    for index, shot in enumerate(payload.get("shots", []), start=1):
        shot_type = str(shot.get("shot_type") or "hero_product").strip().lower().replace("-", "_")
        shot_type = shot_type_aliases.get(shot_type, shot_type)
        if shot_type not in {"hero_product", "benefit_cutaway", "endcard", "establishing", "wide", "medium", "closeup"}:
            shot_type = "hero_product"
        shots.append(
            {
                "shot_id": str(shot.get("shot_id") or f"shot{index:03d}"),
                "sequence_index": index,
                "shot_type": shot_type,
                "duration_sec": min(max(int(shot.get("duration_sec", 3)), 2), 8),
                "audio_mode": "speech" if str(shot.get("audio_mode") or "").strip().lower() == "speech" else "ambience",
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

    non_talking = [shot for shot in shots if shot["shot_type"] != "talking_presenter"]
    while len(non_talking) < 5:
        seed = non_talking[-1] if non_talking else shots[0]
        cloned = clone_shot_with_role(seed, "hero_product", len(non_talking) + 1, "product bridge")
        non_talking.append(cloned)

    middle = non_talking[:5]
    middle_types = ["hero_product", "hero_product", "benefit_cutaway", "hero_product", "endcard"]
    middle_roles = [
        "opening visual hook",
        "hero product reveal",
        "texture or usage detail",
        "benefit or payoff shot",
        "closing packshot",
    ]

    arc = []
    for index, shot in enumerate(middle[:5], start=1):
        cloned = clone_shot_with_role(shot, middle_types[index - 1], index, middle_roles[index - 1])
        cloned["duration_sec"] = 4 if index not in {1, 5} else 3
        cloned["audio_mode"] = "ambience"
        arc.append(cloned)
    return arc


def clone_shot_with_role(source: dict, shot_type: str, sequence_index: int, role_hint: str) -> dict:
    cloned = dict(source)
    cloned["shot_id"] = f"shot{sequence_index:03d}"
    cloned["sequence_index"] = sequence_index
    cloned["shot_type"] = shot_type
    cloned["audio_mode"] = "ambience" if shot_type != "talking_presenter" else "speech"
    cloned["role_hint"] = role_hint
    return cloned


def compose_seedance_prompt(shot: dict, product_brief: dict) -> str:
    shot_type = str(shot.get("shot_type") or "").strip().lower()
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
        (
            f"Luxury {product_brief['product_category']} commercial product shot for "
            f"{product_brief['product_name']} with premium motion, stable product identity, "
            "and precise label visibility."
        ),
        build_seedance_vfx_phrase(shot_type),
        "No readable text, no subtitles, no captions, no UI typography, no letters, no watermark, no logos rendered into the scene.",
    ]
    return ". ".join(part for part in parts if part)


def compose_veo_prompt(shot: dict, product_brief: dict) -> str:
    parts = [
        sanitize_seedance_text(shot.get("appearance_prompt", "").strip()),
        sanitize_seedance_text(shot.get("motion_prompt", "").strip()),
        sanitize_seedance_text(shot.get("camera_prompt", "").strip()),
        (
            f"Image-to-video commercial motion for {product_brief['product_name']} in the "
            f"{product_brief['product_category']} category."
        ),
        "Preserve the supplied image composition and visible text faithfully.",
        "Add subtle realistic motion only, with no extra typography, no logo redesign, and no new UI elements.",
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
        "No visible captions, no on-screen text, no subtitle burn-in, no logos, no readable letters in the frame.",
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
        "No readable text, no title graphics, no subtitles, and no logo text rendered into the image.",
    ]
    return ". ".join(part for part in parts if part)


def build_presenter_profile(voice_id: str) -> dict:
    female_voices = {"Joanna", "Amy", "Ruth", "Salli", "Kajal"}
    if voice_id in female_voices:
        return {
            "gender": "female",
            "label": "female presenter",
            "descriptor": "elegant confident Indian woman",
            "styling": "Refined Indian beauty styling, expressive eyes, camera-ready hair, assured smile",
            "origin": "an Indian woman spokesperson",
        }
    return {
        "gender": "male",
        "label": "male presenter",
        "descriptor": "handsome confident Indian man",
        "styling": "Clean Indian grooming, camera-ready hair, assured smile, trustworthy spokesperson presence",
        "origin": "an Indian man spokesperson",
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
        project_id=project_id,
        shot_id=shot_id,
        script_text=script_text,
        voice_id=voice_id,
    )
    return output_path

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
        "-af",
        "apad",
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    return output_path


def download_remote_file(url: str, target_path: Path) -> Path:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with httpx.stream("GET", url, timeout=None) as response:
                response.raise_for_status()
                with target_path.open("wb") as file_handle:
                    for chunk in response.iter_bytes():
                        file_handle.write(chunk)
            return target_path
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(3 * (attempt + 1))
    raise last_error if last_error else RuntimeError(f"Failed to download remote file from {url}")


def data_uri_from_storage_key(
    *,
    settings: Settings,
    storage: S3Storage,
    project_id: str,
    key: str,
    cache_dir: str,
) -> str:
    suffix = Path(key).suffix or ".bin"
    local_path = get_runtime_path(settings, cache_dir, project_id, f"{Path(key).stem}{suffix}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.exists():
        storage.download_file(key, str(local_path))
    return file_to_data_uri(local_path)


def file_to_data_uri(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


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


def build_seedance_vfx_phrase(shot_type: str) -> str:
    if shot_type == "benefit_cutaway":
        return (
            "Premium cinematic VFX with elegant water simulation, luminous reflections, "
            "soft atmospheric particles, and polished ad-film finishing."
        )
    return (
        "Premium cinematic VFX with controlled liquid sparkle, glossy reflections, "
        "subtle volumetric lighting, and refined luxury ad-film finishing."
    )


def post_process_commercial(
    *,
    settings: Settings,
    storage: S3Storage,
    project_id: str,
    stitched_path: Path,
    shots: list[dict],
    supers: list[str],
    product_image_key: str,
    product_name: str,
) -> Path:
    width, height = probe_video_dimensions(stitched_path)
    processed_path = stitched_path.with_name(f"{stitched_path.stem}.gfx{stitched_path.suffix}")
    overlay_filter = build_overlay_filter(width=width, height=height, shots=shots, supers=supers)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(stitched_path),
        "-vf",
        overlay_filter,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(processed_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])

    packshot_path = get_runtime_path(settings, "packshots", project_id, Path(product_image_key).name)
    packshot_path.parent.mkdir(parents=True, exist_ok=True)
    storage.download_file(product_image_key, str(packshot_path))

    endcard_path = stitched_path.with_name("endcard.mp4")
    create_endcard_video(
        packshot_path=packshot_path,
        output_path=endcard_path,
        width=width,
        height=height,
        product_name=product_name,
        supers=supers,
    )

    final_visual_path = stitched_path.with_name(f"{stitched_path.stem}.final{stitched_path.suffix}")
    concat_visuals([processed_path, endcard_path], final_visual_path)
    return final_visual_path


def probe_video_dimensions(video_path: Path) -> tuple[int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(video_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    return int(stream["width"]), int(stream["height"])


def build_overlay_filter(*, width: int, height: int, shots: list[dict], supers: list[str]) -> str:
    return "null"


def create_endcard_video(
    *,
    packshot_path: Path,
    output_path: Path,
    width: int,
    height: int,
    product_name: str,
    supers: list[str],
) -> None:
    vf = ",".join(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#f4efe7",
            "eq=saturation=1.03:contrast=1.02:brightness=0.01",
            "gblur=sigma=0.3",
        ]
    )
    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(packshot_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=stereo",
        "-t",
        "3",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])


def concat_visuals(segment_paths: list[Path], output_path: Path) -> None:
    concat_path = output_path.with_suffix(".concat.txt")
    concat_path.write_text(
        "".join(f"file '{path.resolve().as_posix()}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])


def ffmpeg_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def ffmpeg_font_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def checkpoint_path(settings: Settings, project_id: str) -> Path:
    path = get_runtime_path(settings, "checkpoints", project_id, "hq-commercial.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_hq_checkpoint(settings: Settings, project_id: str) -> dict:
    path = checkpoint_path(settings, project_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_hq_checkpoint(settings: Settings, project_id: str, payload: dict) -> None:
    checkpoint_path(settings, project_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_segment_video(*, source_path: Path, width: int, height: int) -> Path:
    normalized_path = source_path.with_name(f"{source_path.stem}.norm{source_path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        ),
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        str(normalized_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])
    source_path.unlink(missing_ok=True)
    normalized_path.replace(source_path)
    return source_path


def recover_existing_segments(*, project_id: str, shots: list[dict], outputs_root: Path) -> list[StitchSegmentRef]:
    recovered: list[StitchSegmentRef] = []
    for shot in shots:
        shot_id = shot["shot_id"]
        local_segment_path = outputs_root / f"{shot_id}.mp4"
        if not local_segment_path.exists():
            continue
        engine = choose_shot_engine(shot)
        recovered.append(
            StitchSegmentRef(
                shot_id=shot_id,
                sequence_index=shot.get("sequence_index"),
                job_id=f"recovered-{shot_id}",
                job_type="generate_preview",
                backend_hint=engine,
                duration_sec=shot.get("duration_sec", 4),
                transition="hard_cut",
                output_key=f"previews/{project_id}/{shot_id}.mp4",
            )
        )
    return recovered

"""
Talking-head pipeline
─────────────────────
1. Plan script     → OpenRouter (Gemini 2.0 Flash)
2. Synthesize TTS  → Sarvam AI  (bulbul:v3, male/female)
3. Animate face    → RunPod InfiniteTalk  (image + audio → lip-synced MP4)
4. Upload artefacts → S3
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx

from text2video.local_storage import LocalAssetStore
from text2video.commercial_hq.pipeline import (
    download_remote_file,
    extract_public_video_url,
)
from text2video.commercial_hq.public_endpoints import RunpodPublicEndpointClient
from text2video.commercial_nextgen.openrouter_client import OpenRouterClient
from text2video.config import Settings, get_runtime_path
from text2video.tts.sarvam import synthesize_sarvam_tts_mp3


# ─── Sarvam TTS speakers ────────────────────────────────────────────────────

SARVAM_MALE_VOICES = {
    "aditya": "Aditya (Hindi-inflected EN)",
    "anand": "Anand (neutral EN)",
    "arjun": "Arjun (deep EN)",
    "amol": "Amol (warm EN)",
    "abhur": "Abhirup (crisp EN)",
}

SARVAM_FEMALE_VOICES = {
    "priya": "Priya (warm EN)",
    "anushka": "Anushka (bright EN)",
    "amelia": "Amelia (clear EN)",
    "diya": "Diya (soft EN)",
    "meera": "Meera (expressive EN)",
}


# ─── OpenRouter script planner ──────────────────────────────────────────────

TALKING_HEAD_SCRIPT_SYSTEM_PROMPT = """\
You are a scriptwriter for a short talking-head video.
Given a topic / context, write a natural, conversational spoken script
for a single on-camera presenter.

Constraints:
1. Return valid JSON only.
2. The script should be between 30 and 45 seconds of speech (approx 75-120 words).
3. No stage directions or brackets (e.g., [smiles]); only spoken text.
4. Language: Use the specified language script (English, Hinglish, or Devanagari Hindi) as requested.

JSON Schema:
{
  "script": "The full spoken text.",
  "duration_hint_sec": number,
  "key_points": ["point 1", "point 2", "point 3"]
}
"""



def plan_talking_head_script(
    *,
    settings: Settings,
    topic: str,
    context: str,
    tone: str,
    target_audience: str,
    language_code: str = "hi-IN",
) -> dict:

    """Call OpenRouter to produce a script for the talking head."""
    client = OpenRouterClient(settings)
    user_payload = {
        "topic": topic,
        "context": context or "No additional context provided.",
        "tone": tone or "Professional, engaging, conversational",
        "target_audience": target_audience or "General audience",
        "language_preference": "Devanagari Hindi" if language_code == "hi-IN" else "Indian English",
    }

    return client.chat_json(
        model="google/gemini-2.0-flash-001",
        system_prompt=TALKING_HEAD_SCRIPT_SYSTEM_PROMPT,
        user_payload=user_payload,
    )



# ─── Sarvam TTS ─────────────────────────────────────────────────────────────


def synthesize_sarvam_audio(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    script_text: str,
    speaker: str,
    language_code: str = "hi-IN",
) -> Path:
    """Synthesise script_text with Sarvam TTS and save as MP3. Returns path."""
    return synthesize_sarvam_tts_mp3(
        settings=settings,
        project_id=project_id,
        shot_id=shot_id,
        script_text=script_text,
        voice_id=sarvam_speaker,
        language_code=language_code,
    )


# ─── Main pipeline ──────────────────────────────────────────────────────────


def run_talking_head_pipeline(
    *,
    settings: Settings,
    project_id: str,
    presenter_image_key: str,
    topic: str = "",
    script: str = "",
    context: str = "",
    tone: str = "",
    target_audience: str = "",
    gender: str,           # "male" | "female"
    sarvam_speaker: str,   # e.g. "aditya", "priya"
    language_code: str = "hi-IN",
    resolution: str = "480p",

    output_key: str = "",
) -> dict:
    store = LocalAssetStore(settings, base_url=f"{settings.base_public_url}/assets")
    public_client = RunpodPublicEndpointClient(settings)
    shot_id = "talking-head"

    # ── 1. Plan or Use manual script ────────────────────────────────────────
    if script:
        script_text = script
        duration_hint = len(script_text.split()) // 2  # Rough estimate
        key_points = ["Manual script provided"]
    else:
        script_plan = plan_talking_head_script(
            settings=settings,
            topic=topic,
            context=context,
            tone=tone,
            target_audience=target_audience,
            language_code=language_code,
        )

        script_text = script_plan.get("script", topic)
        duration_hint = int(script_plan.get("duration_hint_sec", 30))
        key_points = script_plan.get("key_points", [])

    # ── 2. Synthesise TTS audio ─────────────────────────────────────────────

    audio_path = synthesize_sarvam_audio(
        settings=settings,
        project_id=project_id,
        shot_id=shot_id,
        script_text=script_text,
        speaker=sarvam_speaker,
        language_code=language_code,
    )

    audio_key = f"audio/{project_id}/{shot_id}.mp3"
    audio_uri = store.upload_file(str(audio_path), audio_key)
    audio_url = store.create_presigned_download(audio_key)["url"]

    # ── 3. Get presenter image URL ──────────────────────────────────────────
    presenter_image_url = store.create_presigned_download(
        presenter_image_key
    )["url"]


    # ── 4. Run InfiniteTalk on RunPod ───────────────────────────────────────
    infinitetalk_prompt = (
        f"Natural {gender} presenter delivering a professional talking-head video. "
        "Clear lip sync, confident eye contact, minimal head movement, "
        "premium studio lighting, polished spokesperson energy."
    )
    print(f"DEBUG: Sending to RunPod: Image={presenter_image_url}, Audio={audio_url}")
    sync_payload = public_client.generate_infinitetalk(

        prompt=infinitetalk_prompt,
        image_url=presenter_image_url,
        audio_url=audio_url,
        resolution=resolution,
    )
    video_url = extract_public_video_url(sync_payload)

    # ── 5. Download + upload final video ────────────────────────────────────
    out_dir = get_runtime_path(settings, "outputs", project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    local_video = out_dir / f"{shot_id}.mp4"
    download_remote_file(video_url, local_video)

    final_output_key = output_key or f"talking_head/{project_id}/{shot_id}.mp4"
    video_uri = store.upload_file(str(local_video), final_output_key)


    return {
        "project_id": project_id,
        "script": script_text,
        "key_points": key_points,
        "duration_hint_sec": duration_hint,
        "speaker": sarvam_speaker,
        "gender": gender,
        "audio_key": audio_key,
        "presenter_image_key": presenter_image_key,
        "output_key": final_output_key,
        "output_uri": video_uri,
        "infinitetalk_cost": sync_payload.get("output", {}).get("cost"),
        "local_video_path": str(local_video),
    }

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from text2video.config import Settings, get_runtime_path


def synthesize_sarvam_tts_mp3(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    script_text: str,
    voice_id: str,
    language_code: str = "en-IN",
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
            "target_language_code": language_code,
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

    out_dir = get_runtime_path(settings, "voiceover", project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{shot_id}.mp3"
    out_path.write_bytes(base64.b64decode(audios[0]))
    return out_path


def sarvam_speaker_for_voice(voice_id: str) -> str:
    mapping = {
        "Matthew": "aditya",
        "Brian": "anand",
        "Joanna": "priya",
        "Amy": "amelia",
    }
    return mapping.get(voice_id, "aditya")

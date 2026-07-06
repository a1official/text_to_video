from __future__ import annotations

from pathlib import Path

import httpx

from text2video.config import Settings, get_runtime_path


def synthesize_elevenlabs_voice_line(
    *,
    settings: Settings,
    project_id: str,
    shot_id: str,
    script_text: str,
    voice_id: str,
) -> Path:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not configured.")
    output_dir = get_runtime_path(settings, "voiceover", project_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{shot_id}.mp3"
    provider_voice_id = elevenlabs_voice_for_voice(voice_id)
    response = httpx.post(
        f"{settings.elevenlabs_base_url.rstrip('/')}/text-to-speech/{provider_voice_id}",
        headers={
            "xi-api-key": settings.elevenlabs_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": " ".join(script_text.split()) or "Premium product commercial.",
            "model_id": settings.elevenlabs_tts_model_id,
            "output_format": "mp3_44100_128",
            "voice_settings": {
                "stability": 0.35,
                "similarity_boost": 0.7,
                "style": 0.35,
                "use_speaker_boost": True,
            },
        },
        timeout=180,
    )
    if response.status_code == 401:
        raise RuntimeError("ELEVENLABS_API_KEY is invalid or expired.")
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def elevenlabs_voice_for_voice(voice_id: str) -> str:
    mapping = {
        "Matthew": "pNInz6obpgDQGcFmaJgB",
        "Brian": "TxGEqnHWrfWFTfGW9XjX",
        "Joanna": "EXAVITQu4vr4xnSDxMaL",
        "Amy": "21m00Tcm4TlvDq8ikWAM",
    }
    return mapping.get(voice_id, "pNInz6obpgDQGcFmaJgB")

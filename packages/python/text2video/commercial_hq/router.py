from __future__ import annotations


def choose_shot_engine(shot: dict) -> str:
    shot_type = str(shot.get("shot_type") or "").strip().lower()
    audio_mode = str(shot.get("audio_mode") or "").strip().lower()
    voiceover_line = str(shot.get("voiceover_line") or "").strip()

    if shot_type in {"talking_head", "talking_presenter"}:
        return "infinitetalk"
    if audio_mode in {"speech", "speech_and_ambience"} and voiceover_line:
        return "infinitetalk"
    return "seedance"

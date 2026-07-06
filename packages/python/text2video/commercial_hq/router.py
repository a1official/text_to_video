from __future__ import annotations


def choose_shot_engine(shot: dict) -> str:
    explicit = str(shot.get("engine") or shot.get("backend_hint") or shot.get("visual_engine") or "").strip().lower()
    if explicit in {"seedance", "veo", "nano_banana_2_edit"}:
        return explicit

    shot_type = str(shot.get("shot_type") or "").strip().lower()
    audio_mode = str(shot.get("audio_mode") or "").strip().lower()
    voiceover_line = str(shot.get("voiceover_line") or "").strip()
    role_hint = str(shot.get("role_hint") or "").strip().lower()
    quality_tier = str(shot.get("quality_tier") or "").strip().lower()

    if quality_tier == "hero" or shot_type in {"hero_product", "benefit_cutaway", "endcard"}:
        return "veo"
    if shot_type in {"talking_head", "talking_presenter"} or role_hint in {"opening presenter hook", "closing presenter close"}:
        return "seedance"
    if audio_mode in {"speech", "speech_and_ambience"} and voiceover_line:
        return "seedance"
    return "seedance"


def choose_image_edit_engine(shot: dict) -> str:
    explicit = str(shot.get("image_edit_engine") or shot.get("edit_engine") or "").strip().lower()
    if explicit in {"nano_banana_2_edit", "nanobanana", "nano_banana"}:
        return "nano_banana_2_edit"
    if str(shot.get("requires_image_edit") or "").strip().lower() in {"1", "true", "yes"}:
        return "nano_banana_2_edit"
    return "nano_banana_2_edit"

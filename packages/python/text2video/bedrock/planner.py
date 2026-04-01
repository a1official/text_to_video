import json
import os
import re

from text2video.aws.session import build_boto3_session
from text2video.config import Settings


class ShotPlanner:
    VALID_BACKENDS = {"wan", "humo", "ltx"}
    VALID_QUALITY_TIERS = {"preview", "hero"}
    VALID_SHOT_TYPES = {
        "establishing",
        "wide",
        "medium",
        "closeup",
        "talking_head",
        "action",
        "transition",
        "insert",
    }
    VALID_AUDIO_MODES = {"none", "ambience", "speech", "music", "speech_and_ambience"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.bedrock_api_key:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.bedrock_api_key

        self.client = build_boto3_session(settings).client(
            "bedrock-runtime",
            region_name=settings.bedrock_region,
        )

    def plan_project(self, project_id: str, prompt: str, references: list[dict]) -> dict:
        system_prompt = (
            "You are a film planner for an AI video generation pipeline. "
            "Return valid JSON only with keys: summary, continuity, shots. "
            "The summary must be a string. "
            "The continuity field must be an array of short strings. "
            "The shots field must be an array. "
            "Each shot must include: shot_id, shot_type, duration_sec, backend_hint, audio_mode, "
            "appearance_prompt, motion_prompt, camera_prompt, quality_tier. "
            "Allowed backend_hint values: wan, humo, ltx. "
            "Allowed quality_tier values: preview, hero. "
            "Allowed shot_type values: establishing, wide, medium, closeup, talking_head, action, transition, insert. "
            "Allowed audio_mode values: none, ambience, speech, music, speech_and_ambience. "
            "When the user asks for a product commercial or ad, plan a sequence of short shots that can be stitched into a longer video. "
            "Use sequence_index ordering implicitly by the order of shots in the array. "
            "Favor ltx for preview/commercial shots unless the user explicitly asks for wan or humo. "
            "If references are provided, preserve product identity, packaging colors, label text, and hero product framing in the prompts. "
            "For spokesperson-style ad shots, describe the presenter holding or presenting the product clearly in appearance_prompt and motion_prompt. "
            "Return no markdown and no explanation outside the JSON."
        )
        user_payload = {
            "project_id": project_id,
            "prompt": prompt,
            "references": references,
        }

        response = self.client.converse(
            modelId=self.settings.bedrock_model_id,
            inferenceConfig={
                "temperature": self.settings.bedrock_temperature,
                "maxTokens": self.settings.bedrock_max_tokens,
            },
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": json.dumps(user_payload)}]}],
        )
        text = response["output"]["message"]["content"][0]["text"]
        return self._normalize_plan(self._parse_json_response(text))

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        cleaned = text.strip()

        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))

        if cleaned.startswith("{") and cleaned.endswith("}"):
            return json.loads(cleaned)

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])

        raise ValueError(f"Bedrock planner did not return parseable JSON: {cleaned[:300]}")

    def _normalize_plan(self, payload: dict) -> dict:
        summary = str(payload.get("summary", "")).strip()
        continuity_raw = payload.get("continuity", [])
        shots_raw = payload.get("shots", [])

        continuity = [str(item).strip() for item in continuity_raw if str(item).strip()]
        normalized_shots = []

        for index, shot in enumerate(shots_raw, start=1):
            normalized_shots.append(self._normalize_shot(index=index, shot=shot))

        return {
            "summary": summary,
            "continuity": continuity,
            "shots": normalized_shots,
        }

    def _normalize_shot(self, index: int, shot: dict) -> dict:
        shot_id = str(shot.get("shot_id") or f"shot{index:03d}").strip()
        shot_type = self._normalize_shot_type(shot.get("shot_type"))
        backend_hint = self._normalize_backend(shot.get("backend_hint"), shot_type)
        duration_sec = self._normalize_duration(shot.get("duration_sec"))
        audio_mode = self._normalize_audio_mode(shot.get("audio_mode"), shot_type)

        return {
            "shot_id": shot_id,
            "sequence_index": index,
            "shot_type": shot_type,
            "duration_sec": duration_sec,
            "backend_hint": backend_hint,
            "camera": str(shot.get("camera") or shot.get("camera_prompt") or "").strip(),
            "prompt": str(
                shot.get("prompt")
                or shot.get("appearance_prompt")
                or shot.get("motion_prompt")
                or ""
            ).strip(),
            "appearance_prompt": str(
                shot.get("appearance_prompt")
                or shot.get("prompt")
                or ""
            ).strip(),
            "motion_prompt": str(shot.get("motion_prompt") or shot.get("prompt") or "").strip(),
            "camera_prompt": str(shot.get("camera_prompt") or shot.get("camera") or "").strip(),
            "quality_tier": self._normalize_quality_tier(shot.get("quality_tier"), backend_hint),
            "audio_mode": audio_mode,
        }

    def _normalize_backend(self, raw_value: object, shot_type: str) -> str:
        value = str(raw_value or "").strip().lower()

        if any(token in value for token in ("humo", "human", "speech", "talk")):
            return "humo"
        if "ltx" in value:
            return "ltx"
        if "wan" in value:
            return "wan"
        return "ltx"

    def _normalize_shot_type(self, raw_value: object) -> str:
        value = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "establishing_shot": "establishing",
            "wide_shot": "wide",
            "medium_shot": "medium",
            "close_up": "closeup",
            "closeup_shot": "closeup",
            "close": "closeup",
            "dialogue": "talking_head",
            "speaking": "talking_head",
            "talking": "talking_head",
        }
        mapped = aliases.get(value, value)
        if mapped in self.VALID_SHOT_TYPES:
            return mapped
        return "wide"

    def _normalize_quality_tier(self, raw_value: object, backend_hint: str) -> str:
        value = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if value in self.VALID_QUALITY_TIERS:
            return value
        return "hero" if backend_hint in {"wan", "humo"} else "preview"

    def _normalize_audio_mode(self, raw_value: object, shot_type: str) -> str:
        value = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "silent": "none",
            "dialogue": "speech",
            "talking": "speech",
            "voice": "speech",
            "speech+ambience": "speech_and_ambience",
            "speech_ambience": "speech_and_ambience",
        }
        mapped = aliases.get(value, value)
        if mapped in self.VALID_AUDIO_MODES:
            return mapped
        if shot_type == "talking_head":
            return "speech"
        return "ambience"

    @staticmethod
    def _normalize_duration(raw_value: object) -> int:
        try:
            duration = int(float(raw_value))
        except (TypeError, ValueError):
            duration = 5
        return min(max(duration, 2), 12)

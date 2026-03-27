from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "text2video"
    app_env: str = "dev"
    runtime_root: str = "runtime"

    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_default_region: str = "us-east-1"

    s3_bucket: str = ""
    dynamodb_projects_table: str = "t2v-projects"
    dynamodb_jobs_table: str = "t2v-jobs"
    dynamodb_outputs_table: str = "t2v-outputs"
    dynamodb_continuity_table: str = "t2v-continuity"

    bedrock_api_key: str | None = None
    bedrock_region: str = "us-east-1"
    bedrock_model_id: str = "us.amazon.nova-pro-v1:0"
    bedrock_temperature: float = 0.2
    bedrock_max_tokens: int = 2000

    runpod_api_key: str | None = None
    runpod_endpoint_id: str | None = None
    runpod_pod_id: str | None = None
    runpod_network_volume_id: str | None = None
    runpod_inference_base_url: str | None = None
    runpod_wan_inference_base_url: str | None = None
    runpod_ltx_inference_base_url: str | None = None
    runpod_request_timeout_sec: int = 3600
    ltx_model_id: str = "Lightricks/LTX-2.3"
    ltx_repo_root: str = "/workspace/text2video/ltx2-official"
    ltx_python_bin: str = "/workspace/text2video/ltx2-official/.venv/bin/python"
    ltx_assets_root: str = "/workspace/models/ltx/LTX-2.3"
    ltx_checkpoint_path: str = "/workspace/models/ltx/LTX-2.3/ltx-2.3-22b-distilled.safetensors"
    ltx_spatial_upsampler_path: str = (
        "/workspace/models/ltx/LTX-2.3/ltx-2.3-spatial-upscaler-x2-1.0.safetensors"
    )
    ltx_gemma_root: str = "/workspace/models/ltx/gemma-3-12b-it-qat-q4_0-unquantized"

    worker_id: str = "local-worker"
    worker_type: Literal["general", "wan", "humo", "stitch"] = "general"
    worker_poll_interval_sec: int = 10
    worker_lease_seconds: int = 600
    worker_heartbeat_seconds: int = 60

    allowed_job_types: list[str] = Field(
        default_factory=lambda: [
            "plan_project",
            "generate_keyframe_sdxl",
            "generate_preview",
            "generate_segment_wan",
            "generate_segment_humo",
            "score_segment",
            "stitch_segments",
            "restore_output",
        ]
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_runtime_path(settings: Settings, *parts: str) -> Path:
    root = Path.cwd() / settings.runtime_root
    return root.joinpath(*parts)

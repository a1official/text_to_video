from __future__ import annotations

from abc import ABC, abstractmethod

from text2video.aws.s3 import S3Storage
from text2video.config import get_settings
from text2video.runpod.client import RunpodInferenceClient
from text2video.runpod.schemas import LtxGenerateRequest, SdxlGenerateRequest, WanGenerateRequest
from text2video.worker.contracts import RenderWorkerPayload, StitchWorkerPayload, WorkerExecutionResult
from text2video.worker.stitch import run_ffmpeg_stitch


class WorkerAdapter(ABC):
    name: str
    supported_job_types: set[str]

    @abstractmethod
    def execute(self, job: dict) -> WorkerExecutionResult:
        raise NotImplementedError


class SdxlImageAdapter(WorkerAdapter):
    name = "sdxl"
    supported_job_types = {"generate_keyframe_sdxl"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        settings = get_settings()
        wan_base_url = settings.runpod_wan_inference_base_url or settings.runpod_inference_base_url
        if wan_base_url:
            storage = S3Storage(settings)
            output_key = payload.keyframe_output_key or f"keyframes/{payload.project_id}/{payload.shot_id}.png"
            response = RunpodInferenceClient(settings, base_url=wan_base_url).generate_sdxl_keyframe(
                SdxlGenerateRequest(
                    project_id=payload.project_id,
                    shot_id=payload.shot_id,
                    prompt=payload.appearance_prompt or payload.prompt,
                    output_key=output_key,
                    upload_url=storage.create_presigned_upload(output_key, expires_in=3600)["url"],
                )
            )
            return WorkerExecutionResult(
                output_type=response.output_type,
                s3_key=response.s3_key,
                resolution=response.resolution,
                backend=response.backend,
                notes=response.notes,
            )
        return WorkerExecutionResult(
            output_type="keyframe_image",
            s3_key=payload.keyframe_output_key or f"keyframes/{payload.project_id}/{payload.shot_id}.png",
            resolution="1280x720",
            backend="sdxl",
            notes="Stub SDXL image adapter validated payload. Real image generation remains disabled locally.",
        )


class WanAdapter(WorkerAdapter):
    name = "wan"
    supported_job_types = {"generate_segment_wan"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        if payload.render_mode == "ti2v" and not payload.source_image_key:
            raise ValueError("Wan TI2V jobs require source_image_key")
        settings = get_settings()
        wan_base_url = settings.runpod_wan_inference_base_url or settings.runpod_inference_base_url
        if wan_base_url:
            storage = S3Storage(settings)
            output_key = f"renders/{payload.project_id}/{payload.shot_id}.mp4"
            response = RunpodInferenceClient(settings, base_url=wan_base_url).generate_wan_ti2v(
                WanGenerateRequest(
                    project_id=payload.project_id,
                    shot_id=payload.shot_id,
                    prompt=_compose_render_prompt(payload),
                    source_image_key=payload.source_image_key,
                    source_image_url=storage.create_presigned_download(
                        payload.source_image_key,
                        expires_in=3600,
                    )["url"],
                    output_key=output_key,
                    upload_url=storage.create_presigned_upload(output_key, expires_in=3600)["url"],
                )
            )
            return WorkerExecutionResult(
                output_type=response.output_type,
                s3_key=response.s3_key,
                fps=response.fps,
                resolution=response.resolution,
                backend=response.backend,
                notes=response.notes,
            )
        return WorkerExecutionResult(
            output_type="render_segment",
            s3_key=f"renders/{payload.project_id}/{payload.shot_id}.mp4",
            duration_sec=payload.duration_sec,
            fps=24,
            resolution="1280x720",
            backend="wan",
            notes=f"Stub WAN adapter validated payload in {payload.render_mode} mode. Real model execution remains disabled locally.",
        )


class HumoAdapter(WorkerAdapter):
    name = "humo"
    supported_job_types = {"generate_segment_humo"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        return WorkerExecutionResult(
            output_type="render_segment",
            s3_key=f"renders/{payload.project_id}/{payload.shot_id}.mp4",
            duration_sec=payload.duration_sec,
            fps=25,
            resolution="1280x720",
            backend="humo",
            notes="Stub HuMo adapter validated payload. Real model execution remains disabled locally.",
        )


class LtxAdapter(WorkerAdapter):
    name = "ltx"
    supported_job_types = {"generate_preview"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        settings = get_settings()
        ltx_base_url = settings.runpod_ltx_inference_base_url
        if ltx_base_url:
            storage = S3Storage(settings)
            output_key = payload.preview_output_key or f"previews/{payload.project_id}/{payload.shot_id}.mp4"
            response = RunpodInferenceClient(settings, base_url=ltx_base_url).generate_ltx_preview(
                LtxGenerateRequest(
                    project_id=payload.project_id,
                    shot_id=payload.shot_id,
                    prompt=_compose_render_prompt(payload),
                    source_image_key=payload.source_image_key,
                    source_image_url=storage.create_presigned_download(
                        payload.source_image_key,
                        expires_in=3600,
                    )["url"],
                    output_key=output_key,
                    upload_url=storage.create_presigned_upload(output_key, expires_in=3600)["url"],
                    num_frames=max(17, payload.duration_sec * 8 + 1),
                )
            )
            return WorkerExecutionResult(
                output_type=response.output_type,
                s3_key=response.s3_key,
                duration_sec=payload.duration_sec,
                fps=response.fps,
                resolution=response.resolution,
                backend=response.backend,
                notes=response.notes,
            )
        return WorkerExecutionResult(
            output_type="preview_segment",
            s3_key=payload.preview_output_key or f"previews/{payload.project_id}/{payload.shot_id}.mp4",
            duration_sec=payload.duration_sec,
            fps=24,
            resolution="1024x576",
            backend="ltx",
            notes=f"Stub LTX preview adapter validated payload in {payload.render_mode} mode. Real model execution remains disabled locally.",
        )


class StitchAdapter(WorkerAdapter):
    name = "stitch"
    supported_job_types = {"stitch_segments"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = StitchWorkerPayload.model_validate(job.get("payload", {}))
        output_path = run_ffmpeg_stitch(get_settings(), payload)
        return WorkerExecutionResult(
            output_type="stitched_scene",
            s3_key=payload.output_key,
            duration_sec=sum(segment.duration_sec for segment in payload.segments),
            fps=24,
            resolution="1280x720",
            backend="stitch",
            manifest_ref=payload.manifest_sk,
            notes=f"FFmpeg stitch completed locally at {output_path}",
        )


def build_adapter_registry() -> dict[str, WorkerAdapter]:
    adapters = [SdxlImageAdapter(), WanAdapter(), HumoAdapter(), LtxAdapter(), StitchAdapter()]
    registry: dict[str, WorkerAdapter] = {}
    for adapter in adapters:
        for job_type in adapter.supported_job_types:
            registry[job_type] = adapter
    return registry


def _compose_render_prompt(payload: RenderWorkerPayload) -> str:
    parts = [
        payload.appearance_prompt.strip(),
        payload.motion_prompt.strip(),
        payload.camera_prompt.strip(),
    ]
    combined = ". ".join(part for part in parts if part)
    return combined or payload.prompt

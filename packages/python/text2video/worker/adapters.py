from __future__ import annotations

from abc import ABC, abstractmethod

from text2video.aws.s3 import S3Storage
from text2video.config import get_settings
from text2video.runpod.client import RunpodInferenceClient
from text2video.runpod.schemas import LtxGenerateRequest, SdxlGenerateRequest, WanGenerateRequest
from text2video.commercial_hq.public_endpoints import RunpodPublicEndpointClient
from text2video.worker.contracts import RenderWorkerPayload, StitchWorkerPayload, WorkerExecutionResult
from text2video.worker.stitch import run_ffmpeg_stitch
from text2video.commercial_hq.pipeline import extract_public_video_url
from text2video.commercial_hq.presenter_generator import (
    download_remote_file,
    resolve_presenter_output,
)


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


class NanoBananaEditAdapter(WorkerAdapter):
    name = "nano_banana_2_edit"
    supported_job_types = {"generate_keyframe_nano_banana_2_edit"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        settings = get_settings()
        storage = S3Storage(settings)
        public_client = RunpodPublicEndpointClient(settings)
        output_key = payload.keyframe_output_key or f"keyframes/{payload.project_id}/{payload.shot_id}-refined.png"
        source_image_url = (
            storage.create_presigned_download(payload.source_image_key, expires_in=3600)["url"]
            if payload.source_image_key
            else _placeholder_image_data_uri()
        )
        completed_payload = public_client.generate_nano_banana_2_edit(
            prompt=_compose_render_prompt(payload),
            images=[source_image_url],
            resolution="2k",
            output_format="png",
            enable_safety_checker=True,
        )
        target_path = storage._local_asset_path(output_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        resolved = resolve_presenter_output(completed_payload or {}, target_path)
        if resolved["source"] == "remote_url":
            download_remote_file(resolved["url"], target_path)
            storage.upload_file(str(target_path), output_key)
        elif resolved["source"] == "inline_bytes":
            target_path.write_bytes(resolved["bytes"])
            storage.upload_file(str(target_path), output_key)
        else:
            raise RuntimeError(f"Nano Banana did not return a usable image payload: {completed_payload}")

        return WorkerExecutionResult(
            output_type="keyframe_image",
            s3_key=output_key,
            resolution="2048x2048",
            backend="nano_banana_2_edit",
            notes="Nano Banana image edit completed.",
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


class VeoAdapter(WorkerAdapter):
    name = "veo"
    supported_job_types = {"generate_segment_veo"}

    def execute(self, job: dict) -> WorkerExecutionResult:
        payload = RenderWorkerPayload.model_validate(job.get("payload", {}))
        if not payload.source_image_key:
            raise ValueError("Veo jobs require source_image_key")

        settings = get_settings()
        storage = S3Storage(settings)
        public_client = RunpodPublicEndpointClient(settings)
        output_key = payload.preview_output_key or f"renders/{payload.project_id}/{payload.shot_id}.mp4"
        source_image_url = storage.create_presigned_download(payload.source_image_key, expires_in=3600)["url"]
        target_duration = _normalize_veo_duration(payload.duration_sec)
        completed_payload = public_client.generate_veo_i2v(
            prompt=_compose_render_prompt(payload),
            image_url=source_image_url,
            duration=target_duration,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=True,
            seed=0,
        )
        video_url = extract_public_video_url(completed_payload)
        local_path = storage._local_asset_path(output_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        download_remote_file(video_url, local_path)
        storage.upload_file(str(local_path), output_key)
        return WorkerExecutionResult(
            output_type="render_segment",
            s3_key=output_key,
            duration_sec=payload.duration_sec,
            fps=24,
            resolution="1280x720",
            backend="veo",
            notes=f"Veo image-to-video completed with duration {target_duration}s.",
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
        settings = get_settings()
        output_path = run_ffmpeg_stitch(settings, payload)
        S3Storage(settings).upload_file(str(output_path), payload.output_key)
        return WorkerExecutionResult(
            output_type="stitched_scene",
            s3_key=payload.output_key,
            duration_sec=sum(segment.duration_sec for segment in payload.segments),
            fps=24,
            resolution="1280x720",
            backend="stitch",
            manifest_ref=payload.manifest_sk,
            notes=f"FFmpeg stitch completed and uploaded to {payload.output_key}",
        )


def build_adapter_registry() -> dict[str, WorkerAdapter]:
    adapters = [
        SdxlImageAdapter(),
        NanoBananaEditAdapter(),
        WanAdapter(),
        VeoAdapter(),
        HumoAdapter(),
        LtxAdapter(),
        StitchAdapter(),
    ]
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


def _placeholder_image_data_uri() -> str:
    return (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2eQAAAAASUVORK5CYII="
    )


def _normalize_veo_duration(duration_sec: int) -> int:
    if duration_sec <= 4:
        return 4
    if duration_sec <= 6:
        return 6
    return 8

from __future__ import annotations

import gc
import os
import subprocess
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx

from text2video.config import get_settings
from text2video.runpod.schemas import (
    HealthResponse,
    InferenceJobAccepted,
    InferenceJobStatus,
    LtxGenerateRequest,
    LtxGenerateResponse,
    SdxlGenerateRequest,
    SdxlGenerateResponse,
    WanGenerateRequest,
    WanGenerateResponse,
)


settings = get_settings()
app = FastAPI(title="Runpod Inference Service", version="0.1.0")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _cleanup_cuda() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


def _upload_file(upload_url: str, file_path: Path, content_type: str) -> None:
    with file_path.open("rb") as file_handle:
        response = httpx.put(
            upload_url,
            content=file_handle.read(),
            headers={"Content-Type": content_type},
            timeout=None,
        )
    if response.is_error:
        raise RuntimeError(
            f"Upload failed with status {response.status_code}: {response.text[:1000]}"
        )


def _download_file(download_url: str, target_path: Path) -> None:
    with httpx.stream("GET", download_url, timeout=None) as response:
        if response.is_error:
            body = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Download failed with status {response.status_code}: {body[:1000]}"
            )
        with target_path.open("wb") as file_handle:
            for chunk in response.iter_bytes():
                file_handle.write(chunk)


def _set_job(job_id: str, **updates: object) -> None:
    with jobs_lock:
        jobs.setdefault(job_id, {"job_id": job_id, "status": "queued", "result": None, "error": ""})
        jobs[job_id].update(updates)


def _get_job(job_id: str) -> dict:
    with jobs_lock:
        if job_id not in jobs:
            raise KeyError(job_id)
        return deepcopy(jobs[job_id])


def _run_job(job_id: str, fn, request) -> None:
    try:
        _set_job(job_id, status="running")
        result = fn(request)
        _set_job(job_id, status="completed", result=result.model_dump())
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    sdxl_model_dir = Path("/workspace/models/sdxl/stable-diffusion-xl-base-1.0")
    wan_repo = Path("/workspace/models/wan/Wan2.2")
    return HealthResponse(
        status="ok",
        sdxl_loaded=sdxl_model_dir.exists(),
        wan_repo_present=wan_repo.exists(),
    )


def _generate_sdxl_keyframe_sync(request: SdxlGenerateRequest) -> SdxlGenerateResponse:
    import torch
    from diffusers import StableDiffusionXLPipeline

    _cleanup_cuda()
    sdxl_model_dir = "/workspace/models/sdxl/stable-diffusion-xl-base-1.0"
    pipe = StableDiffusionXLPipeline.from_pretrained(
        sdxl_model_dir,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        local_files_only=True,
        use_safetensors=True,
        variant="fp16" if torch.cuda.is_available() else None,
    )
    pipe = pipe.to("cuda" if torch.cuda.is_available() else "cpu")

    image = pipe(
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        width=request.width,
        height=request.height,
        num_inference_steps=request.num_inference_steps,
        guidance_scale=request.guidance_scale,
    ).images[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / f"{request.shot_id}.png"
        image.save(output_path)
        _upload_file(request.upload_url, output_path, "image/png")

    del image
    del pipe
    _cleanup_cuda()
    return SdxlGenerateResponse(
        s3_key=request.output_key,
        resolution=f"{request.width}x{request.height}",
        notes="SDXL keyframe generated and uploaded to S3.",
    )


def _generate_wan_ti2v_sync(request: WanGenerateRequest) -> WanGenerateResponse:
    _cleanup_cuda()
    wan_repo = Path("/workspace/models/wan/Wan2.2")
    ckpt_dir = Path("/workspace/models/wan/Wan2.2-TI2V-5B")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_image = tmpdir_path / f"{request.shot_id}.png"
        output_video = tmpdir_path / f"{request.shot_id}.mp4"
        _download_file(request.source_image_url, input_image)

        command = [
            "python",
            str(wan_repo / "generate.py"),
            "--task",
            "ti2v-5B",
            "--size",
            request.size,
            "--ckpt_dir",
            str(ckpt_dir),
            "--offload_model",
            "True",
            "--convert_model_dtype",
            "--t5_cpu",
            "--image",
            str(input_image),
            "--prompt",
            request.prompt,
            "--frame_num",
            str(request.frame_num),
            "--sample_steps",
            str(request.sample_steps),
            "--sample_guide_scale",
            str(request.sample_guide_scale),
            "--base_seed",
            str(request.seed),
            "--save_file",
            str(output_video),
        ]

        if request.sample_shift is not None:
            command.extend(["--sample_shift", str(request.sample_shift)])

        completed = subprocess.run(
            command,
            cwd=str(wan_repo),
            check=False,
            capture_output=True,
            text=True,
        )

        if completed.stdout:
            print(completed.stdout[-4000:])
        if completed.stderr:
            print(completed.stderr[-4000:])

        if completed.returncode != 0:
            details = "\n".join(
                part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
            )
            raise RuntimeError(details or f"Wan generate.py failed with exit code {completed.returncode}")

        _upload_file(request.upload_url, output_video, "video/mp4")

    _cleanup_cuda()
    return WanGenerateResponse(
        s3_key=request.output_key,
        notes="Wan TI2V segment generated and uploaded to S3.",
    )


def _generate_ltx_preview_sync(request: LtxGenerateRequest) -> LtxGenerateResponse:
    import torch
    from diffusers import LTX2ImageToVideoPipeline
    from diffusers.utils import export_to_video
    from PIL import Image

    _cleanup_cuda()

    model_id = settings.ltx_model_id

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_image = tmpdir_path / f"{request.shot_id}.png"
        output_video = tmpdir_path / f"{request.shot_id}.mp4"
        _download_file(request.source_image_url, input_image)

        pipe = LTX2ImageToVideoPipeline.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        )
        if torch.cuda.is_available():
            pipe.enable_model_cpu_offload()
        image = Image.open(input_image).convert("RGB")
        generator = torch.Generator("cuda" if torch.cuda.is_available() else "cpu").manual_seed(request.seed)

        video, _audio = pipe(
            image=image,
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            num_frames=request.num_frames,
            frame_rate=24.0,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=request.guidance_scale,
            generator=generator,
            output_type="pil",
            return_dict=False,
        )
        export_to_video(video[0], str(output_video), fps=24)
        _upload_file(request.upload_url, output_video, "video/mp4")

        del image
        del pipe

    _cleanup_cuda()
    return LtxGenerateResponse(
        s3_key=request.output_key,
        resolution=f"{request.width}x{request.height}",
        notes=f"LTX preview generated with model {model_id} and uploaded to S3.",
    )


@app.post("/sdxl/generate-keyframe", response_model=InferenceJobAccepted)
def generate_sdxl_keyframe(request: SdxlGenerateRequest) -> InferenceJobAccepted:
    job_id = str(uuid4())
    _set_job(job_id, status="queued")
    threading.Thread(target=_run_job, args=(job_id, _generate_sdxl_keyframe_sync, request), daemon=True).start()
    return InferenceJobAccepted(job_id=job_id)


@app.post("/wan/generate-ti2v", response_model=InferenceJobAccepted)
def generate_wan_ti2v(request: WanGenerateRequest) -> InferenceJobAccepted:
    job_id = str(uuid4())
    _set_job(job_id, status="queued")
    threading.Thread(target=_run_job, args=(job_id, _generate_wan_ti2v_sync, request), daemon=True).start()
    return InferenceJobAccepted(job_id=job_id)


@app.post("/ltx/generate-preview", response_model=InferenceJobAccepted)
def generate_ltx_preview(request: LtxGenerateRequest) -> InferenceJobAccepted:
    job_id = str(uuid4())
    _set_job(job_id, status="queued")
    threading.Thread(target=_run_job, args=(job_id, _generate_ltx_preview_sync, request), daemon=True).start()
    return InferenceJobAccepted(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=InferenceJobStatus)
def get_job(job_id: str) -> InferenceJobStatus:
    try:
        return InferenceJobStatus.model_validate(_get_job(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

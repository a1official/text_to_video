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
    QwenGenerateRequest,
    QwenGenerateResponse,
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
    response.raise_for_status()


def _download_file(download_url: str, target_path: Path) -> None:
    with httpx.stream("GET", download_url, timeout=None) as response:
        response.raise_for_status()
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
    qwen_cache = Path(os.environ.get("HF_HOME", "/workspace/cache/huggingface")) / "hub"
    wan_repo = Path("/workspace/models/wan/Wan2.2")
    return HealthResponse(
        status="ok",
        qwen_loaded=qwen_cache.exists(),
        wan_repo_present=wan_repo.exists(),
    )


def _generate_qwen_keyframe_sync(request: QwenGenerateRequest) -> QwenGenerateResponse:
    import torch
    from diffusers import QwenImagePipeline

    _cleanup_cuda()
    qwen_model_dir = "/workspace/models/qwen/Qwen-Image-2512"
    pipe = QwenImagePipeline.from_pretrained(
        qwen_model_dir,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        local_files_only=True,
    )
    pipe = pipe.to("cuda" if torch.cuda.is_available() else "cpu")

    image = pipe(
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        width=request.width,
        height=request.height,
        num_inference_steps=request.num_inference_steps,
        true_cfg_scale=request.true_cfg_scale,
    ).images[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / f"{request.shot_id}.png"
        image.save(output_path)
        _upload_file(request.upload_url, output_path, "image/png")

    del image
    del pipe
    _cleanup_cuda()
    return QwenGenerateResponse(
        s3_key=request.output_key,
        resolution=f"{request.width}x{request.height}",
        notes="Qwen-Image keyframe generated and uploaded to S3.",
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

        subprocess.run(
            command,
            cwd=str(wan_repo),
            check=True,
        )

        _upload_file(request.upload_url, output_video, "video/mp4")

    _cleanup_cuda()
    return WanGenerateResponse(
        s3_key=request.output_key,
        notes="Wan TI2V segment generated and uploaded to S3.",
    )


@app.post("/qwen/generate-keyframe", response_model=InferenceJobAccepted)
def generate_qwen_keyframe(request: QwenGenerateRequest) -> InferenceJobAccepted:
    job_id = str(uuid4())
    _set_job(job_id, status="queued")
    threading.Thread(target=_run_job, args=(job_id, _generate_qwen_keyframe_sync, request), daemon=True).start()
    return InferenceJobAccepted(job_id=job_id)


@app.post("/wan/generate-ti2v", response_model=InferenceJobAccepted)
def generate_wan_ti2v(request: WanGenerateRequest) -> InferenceJobAccepted:
    job_id = str(uuid4())
    _set_job(job_id, status="queued")
    threading.Thread(target=_run_job, args=(job_id, _generate_wan_ti2v_sync, request), daemon=True).start()
    return InferenceJobAccepted(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=InferenceJobStatus)
def get_job(job_id: str) -> InferenceJobStatus:
    try:
        return InferenceJobStatus.model_validate(_get_job(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

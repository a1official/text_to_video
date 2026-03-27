from __future__ import annotations

import gc
import os
import subprocess
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from text2video.config import get_settings
from text2video.runpod.schemas import (
    HealthResponse,
    InferenceJobAccepted,
    InferenceJobStatus,
    LtxGenerateRequest,
    LtxGenerateResponse,
)


settings = get_settings()
app = FastAPI(title="Runpod LTX Service", version="0.1.0")
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
            raise RuntimeError(
                f"Download failed with status {response.status_code}: {response.text[:1000]}"
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


def _required_paths() -> list[Path]:
    return [
        Path(settings.ltx_repo_root),
        Path(settings.ltx_python_bin),
        Path(settings.ltx_checkpoint_path),
        Path(settings.ltx_spatial_upsampler_path),
        Path(settings.ltx_gemma_root),
    ]


def _validate_request(request: LtxGenerateRequest) -> None:
    if request.width % 64 != 0 or request.height % 64 != 0:
        raise ValueError("LTX width and height must be divisible by 64")
    if request.num_frames < 9 or (request.num_frames - 1) % 8 != 0:
        raise ValueError("LTX num_frames must satisfy 8k+1")


def _run_official_ltx(cmd: list[str], cwd: Path) -> None:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode == 0:
        return
    output = "\n".join(
        part.strip() for part in [completed.stdout[-3000:], completed.stderr[-3000:]] if part.strip()
    )
    raise RuntimeError(f"Official LTX runner failed with exit code {completed.returncode}:\n{output}")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        sdxl_loaded=all(path.exists() for path in _required_paths()),
        wan_repo_present=False,
    )


def _generate_ltx_preview_sync(request: LtxGenerateRequest) -> LtxGenerateResponse:
    _validate_request(request)
    _cleanup_cuda()

    repo_root = Path(settings.ltx_repo_root)
    python_bin = Path(settings.ltx_python_bin)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_image = tmpdir_path / f"{request.shot_id}.png"
        output_video = tmpdir_path / f"{request.shot_id}.mp4"
        _download_file(request.source_image_url, input_image)

        command = [
            str(python_bin),
            "-m",
            "ltx_pipelines.distilled",
            "--distilled-checkpoint-path",
            settings.ltx_checkpoint_path,
            "--spatial-upsampler-path",
            settings.ltx_spatial_upsampler_path,
            "--gemma-root",
            settings.ltx_gemma_root,
            "--prompt",
            request.prompt,
            "--output-path",
            str(output_video),
            "--seed",
            str(request.seed),
            "--height",
            str(request.height),
            "--width",
            str(request.width),
            "--num-frames",
            str(request.num_frames),
            "--frame-rate",
            "24",
            "--image",
            str(input_image),
            "0",
            "1.0",
        ]

        _run_official_ltx(command, repo_root)
        _upload_file(request.upload_url, output_video, "video/mp4")

    _cleanup_cuda()
    return LtxGenerateResponse(
        s3_key=request.output_key,
        resolution=f"{request.width}x{request.height}",
        notes="Official LTX-2.3 distilled pipeline generated preview video with audio and uploaded it to S3.",
    )


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

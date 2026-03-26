from __future__ import annotations

import gc
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from PIL import Image

from text2video.aws.s3 import S3Storage
from text2video.config import get_settings
from text2video.runpod.schemas import (
    HealthResponse,
    QwenGenerateRequest,
    QwenGenerateResponse,
    WanGenerateRequest,
    WanGenerateResponse,
)


settings = get_settings()
app = FastAPI(title="Runpod Inference Service", version="0.1.0")
s3_storage = S3Storage(settings)


def _cleanup_cuda() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    qwen_cache = Path(os.environ.get("HF_HOME", "/workspace/cache/huggingface")) / "hub"
    wan_repo = Path("/workspace/models/wan/Wan2.2")
    return HealthResponse(
        status="ok",
        qwen_loaded=qwen_cache.exists(),
        wan_repo_present=wan_repo.exists(),
    )


@app.post("/qwen/generate-keyframe", response_model=QwenGenerateResponse)
def generate_qwen_keyframe(request: QwenGenerateRequest) -> QwenGenerateResponse:
    import torch
    from diffusers import QwenImagePipeline

    _cleanup_cuda()
    pipe = QwenImagePipeline.from_pretrained(
        "Qwen/Qwen-Image-2512",
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
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
        s3_storage.upload_file(str(output_path), request.output_key)

    del image
    del pipe
    _cleanup_cuda()
    return QwenGenerateResponse(
        s3_key=request.output_key,
        resolution=f"{request.width}x{request.height}",
        notes="Qwen-Image keyframe generated and uploaded to S3.",
    )


@app.post("/wan/generate-ti2v", response_model=WanGenerateResponse)
def generate_wan_ti2v(request: WanGenerateRequest) -> WanGenerateResponse:
    _cleanup_cuda()
    wan_repo = Path("/workspace/models/wan/Wan2.2")
    ckpt_dir = Path("/workspace/models/wan/Wan2.2-TI2V-5B")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        input_image = tmpdir_path / f"{request.shot_id}.png"
        output_video = tmpdir_path / f"{request.shot_id}.mp4"
        s3_storage.download_file(request.source_image_key, str(input_image))

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

        s3_storage.upload_file(str(output_video), request.output_key)

    _cleanup_cuda()
    return WanGenerateResponse(
        s3_key=request.output_key,
        notes="Wan TI2V segment generated and uploaded to S3.",
    )

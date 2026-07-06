from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import boto3
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response


app = FastAPI(title="Wav2Lip-fast SageMaker Serverless")

REPO_ROOT = Path(os.getenv("WAV2LIP_REPO_ROOT", "/opt/program/Wav2Lip-fast"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/opt/ml/model"))
TEMP_DIR = Path(os.getenv("TEMP_DIR", "/tmp/wav2lip-fast"))
CHECKPOINT_PATH = Path(os.getenv("WAV2LIP_CHECKPOINT_PATH", MODEL_DIR / "wav2lip.pth"))
OUTPUT_BUCKET = os.getenv("SAGEMAKER_WAV2LIP_OUTPUT_BUCKET", "").strip()
OUTPUT_PREFIX = os.getenv("SAGEMAKER_WAV2LIP_OUTPUT_PREFIX", "wav2lip-fast/outputs").strip().strip("/")
CHECKPOINT_S3_URI = os.getenv("WAV2LIP_CHECKPOINT_S3_URI", "").strip()
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _ensure_dirs() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _download_from_s3_uri(s3_uri: str, target_path: Path) -> Path:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Unsupported S3 URI: {s3_uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _s3_client().download_file(bucket, key, str(target_path))
    return target_path


def _download_to_path(source: str, target_path: Path) -> Path:
    parsed = urlparse(source)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if parsed.scheme == "s3":
        return _download_from_s3_uri(source, target_path)
    if parsed.scheme in {"http", "https"}:
        with httpx.stream("GET", source, timeout=120.0) as response:
            response.raise_for_status()
            with target_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return target_path
    if parsed.scheme == "data":
        header, encoded = source.split(",", 1)
        raw = base64.b64decode(encoded)
        target_path.write_bytes(raw)
        return target_path
    src_path = Path(source)
    if not src_path.exists():
        raise FileNotFoundError(f"Input file not found: {source}")
    shutil.copyfile(src_path, target_path)
    return target_path


def _ensure_checkpoint() -> Path:
    if CHECKPOINT_PATH.exists():
        return CHECKPOINT_PATH
    if CHECKPOINT_S3_URI:
        _download_from_s3_uri(CHECKPOINT_S3_URI, CHECKPOINT_PATH)
        return CHECKPOINT_PATH
    raise FileNotFoundError(
        "Missing Wav2Lip checkpoint. Set WAV2LIP_CHECKPOINT_PATH or WAV2LIP_CHECKPOINT_S3_URI."
    )


def _upload_output(output_path: Path, output_key: str | None = None) -> str:
    if not OUTPUT_BUCKET:
        raise RuntimeError("SAGEMAKER_WAV2LIP_OUTPUT_BUCKET is not configured")
    key = output_key or f"{OUTPUT_PREFIX}/{output_path.name}"
    _s3_client().upload_file(str(output_path), OUTPUT_BUCKET, key)
    return f"s3://{OUTPUT_BUCKET}/{key}"


def _run_wav2lip(face_path: Path, audio_path: Path, output_path: Path) -> None:
    inference_script = REPO_ROOT / "inference.py"
    if not inference_script.exists():
        raise FileNotFoundError(f"Missing inference.py at {inference_script}")

    checkpoint = _ensure_checkpoint()

    command = [
        "python",
        str(inference_script),
        "--checkpoint_path",
        str(checkpoint),
        "--face",
        str(face_path),
        "--audio",
        str(audio_path),
        "--outfile",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Wav2Lip-fast inference failed.\n"
            f"STDOUT:\n{completed.stdout[-3000:]}\n"
            f"STDERR:\n{completed.stderr[-3000:]}"
        )


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/invocations")
async def invocations(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON request: {exc}") from exc

    face_source = (
        payload.get("face_image_url")
        or payload.get("face_url")
        or payload.get("face")
        or payload.get("face_image")
    )
    audio_source = payload.get("audio_url") or payload.get("audio") or payload.get("audio_file_url")
    output_key = payload.get("output_s3_key") or payload.get("output_key")

    if not face_source:
        raise HTTPException(status_code=400, detail="Missing face image/video source")
    if not audio_source:
        raise HTTPException(status_code=400, detail="Missing audio source")

    _ensure_dirs()
    work_dir = Path(tempfile.mkdtemp(prefix="wav2lip-fast-", dir=str(TEMP_DIR)))
    face_path = work_dir / "face_input"
    audio_path = work_dir / "audio_input"
    output_path = work_dir / "output.mp4"

    _download_to_path(str(face_source), face_path)
    _download_to_path(str(audio_source), audio_path)
    _run_wav2lip(face_path=face_path, audio_path=audio_path, output_path=output_path)
    output_s3_uri = _upload_output(output_path, output_key=output_key)

    response_body = {
        "status": "completed",
        "output_s3_uri": output_s3_uri,
        "output_bucket": OUTPUT_BUCKET,
        "output_key": output_s3_uri.replace(f"s3://{OUTPUT_BUCKET}/", "", 1),
    }
    return JSONResponse(response_body)


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "failed", "error": str(exc)},
    )


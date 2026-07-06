from __future__ import annotations

import subprocess
from pathlib import Path

from text2video.config import Settings, get_runtime_path
from text2video.worker.contracts import StitchWorkerPayload


def resolve_segment_path(settings: Settings, project_id: str, output_key: str) -> Path:
    if not output_key:
        raise FileNotFoundError("Segment output_key is empty")
    filename = Path(output_key).name
    return get_runtime_path(settings, "outputs", project_id, filename)


def ensure_segment_file(settings: Settings, project_id: str, output_key: str) -> Path:
    segment_path = resolve_segment_path(settings, project_id, output_key)
    if segment_path.exists():
        return segment_path

    from text2video.aws.s3 import S3Storage

    segment_path.parent.mkdir(parents=True, exist_ok=True)
    S3Storage(settings).download_file(output_key, str(segment_path))
    return segment_path


def run_ffmpeg_stitch(settings: Settings, payload: StitchWorkerPayload) -> Path:
    final_output_path = get_runtime_path(settings, "stitched", payload.project_id, Path(payload.output_key).name)
    final_output_path.parent.mkdir(parents=True, exist_ok=True)

    segment_paths = [ensure_segment_file(settings, payload.project_id, segment.output_key) for segment in payload.segments]
    missing = [str(path) for path in segment_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing local segment files for stitch job: {missing}")

    visual_output_path = final_output_path.with_name(f"{final_output_path.stem}.visual{final_output_path.suffix}")
    all_hard_cuts = all(segment.transition == "hard_cut" for segment in payload.segments)
    if all_hard_cuts:
        concat_path = visual_output_path.with_suffix(".concat.txt")
        concat_path.write_text(
            "".join(f"file '{path.resolve().as_posix()}'\n" for path in segment_paths),
            encoding="utf-8",
        )
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(visual_output_path),
        ]
    else:
        command = _build_xfade_command(segment_paths=segment_paths, payload=payload, output_path=visual_output_path)

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])

    if payload.audio_key:
        audio_path = ensure_audio_file(settings, payload.project_id, payload.audio_key)
        mux_audio_into_video(video_path=visual_output_path, audio_path=audio_path, output_path=final_output_path)
        return final_output_path

    return visual_output_path


def ensure_audio_file(settings: Settings, project_id: str, audio_key: str) -> Path:
    if not audio_key:
        raise FileNotFoundError("Audio key is empty")
    audio_path = get_runtime_path(settings, "voiceover", project_id, Path(audio_key).name)
    if audio_path.exists():
        return audio_path

    from text2video.aws.s3 import S3Storage

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    S3Storage(settings).download_file(audio_key, str(audio_path))
    return audio_path


def mux_audio_into_video(*, video_path: Path, audio_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-filter_complex",
        "[1:a]apad[audio]",
        "-map",
        "0:v:0",
        "-map",
        "[audio]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])


def _build_xfade_command(segment_paths: list[Path], payload: StitchWorkerPayload, output_path: Path) -> list[str]:
    command = ["ffmpeg", "-y"]
    for path in segment_paths:
        command.extend(["-i", str(path)])

    video_labels = []
    for index in range(len(segment_paths)):
        label = f"[{index}:v]"
        command.extend([])
        video_labels.append(label)

    filters = []
    current_label = "[0:v]"
    offset = max(payload.segments[0].duration_sec - 1, 0)
    for index in range(1, len(segment_paths)):
        next_label = f"[{index}:v]"
        out_label = f"[v{index}]"
        transition = payload.segments[index].transition
        ffmpeg_transition = "fade" if transition == "crossfade" else "fade"
        filters.append(
            f"{current_label}{next_label}xfade=transition={ffmpeg_transition}:duration=1:offset={offset}{out_label}"
        )
        current_label = out_label
        offset += max(payload.segments[index].duration_sec - 1, 0)

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            current_label,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(output_path),
        ]
    )
    return command

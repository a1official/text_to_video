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


def run_ffmpeg_stitch(settings: Settings, payload: StitchWorkerPayload) -> Path:
    output_path = get_runtime_path(settings, "stitched", payload.project_id, Path(payload.output_key).name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    segment_paths = [resolve_segment_path(settings, payload.project_id, segment.output_key) for segment in payload.segments]
    missing = [str(path) for path in segment_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing local segment files for stitch job: {missing}")

    all_hard_cuts = all(segment.transition == "hard_cut" for segment in payload.segments)
    if all_hard_cuts:
        concat_path = output_path.with_suffix(".concat.txt")
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
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output_path),
        ]
    else:
        command = _build_xfade_command(segment_paths=segment_paths, payload=payload, output_path=output_path)

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:])

    return output_path


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
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output_path),
        ]
    )
    return command

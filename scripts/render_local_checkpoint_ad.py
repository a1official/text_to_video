from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from text2video.commercial_hq.pipeline import synthesize_sarvam_tts_mp3
from text2video.config import get_runtime_path, get_settings


WIDTH = 1280
HEIGHT = 720
FPS = 24


def main() -> None:
    settings = get_settings()
    project_id = sys.argv[1] if len(sys.argv) > 1 else "travey-club-001"
    checkpoint_path = get_runtime_path(settings, "checkpoints", project_id, "hq-commercial.json")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    source_candidates = [
        get_runtime_path(settings, f"{project_id}-concept-board.png"),
        get_runtime_path(settings, "analysis", project_id, "source-product.png"),
    ]
    source_image = next((path for path in source_candidates if path.exists()), None)
    if source_image is None:
        raise FileNotFoundError("Could not find a local source image for checkpoint rendering.")

    shots = checkpoint["plan"]["shots"]
    product_name = checkpoint["product_brief"]["product_name"]
    voiceover_script = checkpoint["plan"]["voiceover_script"]

    render_root = get_runtime_path(settings, "renders", project_id)
    shot_frames_root = render_root / "frames"
    shot_videos_root = render_root / "segments"
    shot_frames_root.mkdir(parents=True, exist_ok=True)
    shot_videos_root.mkdir(parents=True, exist_ok=True)

    frame_paths: list[Path] = []
    segment_paths: list[Path] = []
    for shot in shots:
        frame_path = shot_frames_root / f"{shot['shot_id']}.png"
        segment_path = shot_videos_root / f"{shot['shot_id']}.mp4"
        render_shot_frame(
            source_image=source_image,
            target_path=frame_path,
            shot=shot,
            product_name=product_name,
        )
        render_motion_segment(
            frame_path=frame_path,
            target_path=segment_path,
            duration=int(shot.get("duration_sec", 4)),
            zoom_mode=zoom_mode_for_shot(shot["shot_id"]),
        )
        frame_paths.append(frame_path)
        segment_paths.append(segment_path)

    stitched_dir = get_runtime_path(settings, "stitched", project_id)
    stitched_dir.mkdir(parents=True, exist_ok=True)
    stitched_path = stitched_dir / "travey-club.local.mp4"
    concat_segments(segment_paths=segment_paths, output_path=stitched_path)

    audio_path = get_runtime_path(settings, "voiceover", project_id, "local-master-voiceover.mp3")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        synthesize_sarvam_tts_mp3(
            settings=settings,
            script_text=voiceover_script,
            voice_id="Matthew",
            target_path=audio_path,
        )
    except Exception:
        audio_path = Path()

    final_path = stitched_dir / "travey-club.local.vo.mp4"
    if audio_path and audio_path.exists():
        mux_audio(video_path=stitched_path, audio_path=audio_path, output_path=final_path)
    else:
        final_path = stitched_path

    result = {
        "project_id": project_id,
        "checkpoint_path": str(checkpoint_path),
        "source_image": str(source_image),
        "stitched_video": str(stitched_path),
        "final_video": str(final_path),
        "audio_path": str(audio_path) if audio_path else "",
        "voiceover_script": voiceover_script,
    }
    print(json.dumps(result, indent=2))


def render_shot_frame(*, source_image: Path, target_path: Path, shot: dict, product_name: str) -> None:
    base = Image.open(source_image).convert("RGB")
    canvas = crop_for_shot(base, shot["shot_id"]).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.3))

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # cinematic top/bottom vignette
    draw.rectangle((0, 0, WIDTH, 120), fill=(10, 20, 28, 120))
    draw.rectangle((0, HEIGHT - 96, WIDTH, HEIGHT), fill=(10, 20, 28, 80))

    composed = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    composed.convert("RGB").save(target_path, quality=95)


def crop_for_shot(image: Image.Image, shot_id: str) -> Image.Image:
    w, h = image.size
    crops = {
        "shot001": (0, 0, w, h),
        "shot002": (w * 0.52, h * 0.15, w * 0.98, h * 0.62),
        "shot003": (w * 0.52, h * 0.22, w * 0.98, h * 0.82),
        "shot004": (w * 0.45, h * 0.24, w * 0.98, h * 0.96),
        "shot005": (w * 0.32, h * 0.02, w * 0.98, h * 0.98),
    }
    left, top, right, bottom = crops.get(shot_id, (0, 0, w, h))
    return image.crop((int(left), int(top), int(right), int(bottom)))


def zoom_mode_for_shot(shot_id: str) -> str:
    mapping = {
        "shot001": "slow_in",
        "shot002": "slow_in",
        "shot003": "float",
        "shot004": "slow_out",
        "shot005": "slow_in",
    }
    return mapping.get(shot_id, "slow_in")


def render_motion_segment(*, frame_path: Path, target_path: Path, duration: int, zoom_mode: str) -> None:
    total_frames = max(duration * FPS, FPS * 2)
    if zoom_mode == "slow_out":
        zoom_expr = "if(lte(on,1),1.12,max(1.0,zoom-0.0007))"
    elif zoom_mode == "float":
        zoom_expr = "if(lte(on,1),1.08,if(gte(zoom,1.12),zoom-0.0004,zoom+0.0004))"
    else:
        zoom_expr = "if(lte(on,1),1.0,min(1.12,zoom+0.0007))"

    filter_chain = (
        f"zoompan=z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "format=yuv420p,"
        f"fade=t=in:st=0:d=0.35,"
        f"fade=t=out:st={max(duration - 0.45, 0)}:d=0.45"
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame_path),
            "-vf",
            filter_chain,
            "-t",
            str(duration),
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            str(target_path),
        ]
    )


def concat_segments(*, segment_paths: list[Path], output_path: Path) -> None:
    concat_path = output_path.with_suffix(".concat.txt")
    concat_path.write_text(
        "".join(f"file '{path.resolve().as_posix()}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    run_command(
        [
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
            str(output_path),
        ]
    )


def mux_audio(*, video_path: Path, audio_path: Path, output_path: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-af",
            "apad",
            "-shortest",
            str(output_path),
        ]
    )


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def run_command(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-2000:])


if __name__ == "__main__":
    main()

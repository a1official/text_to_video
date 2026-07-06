from __future__ import annotations

import argparse
import json

from text2video.commercial_nvidia.pipeline import run_nvidia_commercial
from text2video.config import get_settings


DEFAULT_PROMPT = (
    "Create a premium stitched English commercial with clean product storytelling and cinematic product beauty shots."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NVIDIA-based commercial pipeline.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--product-image-key", required=True)
    parser.add_argument("--brief-mode", choices=("quick", "detailed"), default="quick")
    parser.add_argument("--product-name", default="")
    parser.add_argument("--product-category", default="")
    parser.add_argument("--product-description", default="")
    parser.add_argument("--target-audience", default="")
    parser.add_argument("--key-benefit", action="append", default=[])
    parser.add_argument("--brand-tone", default="Premium, cinematic, English-language commercial")
    parser.add_argument("--call-to-action", default="")
    parser.add_argument("--additional-notes", default="")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-key", default="")
    parser.add_argument("--voice-id", default="Matthew")
    parser.add_argument("--voice-engine", default="neural")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_nvidia_commercial(
        settings=get_settings(),
        project_id=args.project_id,
        product_image_key=args.product_image_key,
        brief_mode=args.brief_mode,
        product_name=args.product_name,
        product_category=args.product_category,
        product_description=args.product_description,
        target_audience=args.target_audience,
        key_benefits=args.key_benefit,
        brand_tone=args.brand_tone,
        call_to_action=args.call_to_action,
        additional_notes=args.additional_notes,
        prompt=args.prompt,
        width=args.width,
        height=args.height,
        seed=args.seed,
        output_key=args.output_key,
        voice_id=args.voice_id,
        voice_engine=args.voice_engine,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

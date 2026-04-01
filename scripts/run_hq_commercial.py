from __future__ import annotations

import argparse
import json

from text2video.commercial_hq.pipeline import run_hq_commercial
from text2video.config import get_settings


DEFAULT_PROMPT = (
    "Create a premium stitched English commercial with better presenter quality. "
    "Use a confident male presenter speaking directly to camera in premium studio lighting, "
    "intercut with luxury product hero shots and a strong closing packshot."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a mixed-engine premium commercial pipeline.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--product-image-key", required=True)
    parser.add_argument("--presenter-image-key", default="")
    parser.add_argument("--brief-mode", choices=("quick", "detailed"), default="quick")
    parser.add_argument("--product-name", default="")
    parser.add_argument("--product-category", default="")
    parser.add_argument("--product-description", default="")
    parser.add_argument("--target-audience", default="")
    parser.add_argument("--key-benefit", action="append", default=[])
    parser.add_argument("--brand-tone", default="Premium, trustworthy, English-language commercial")
    parser.add_argument("--call-to-action", default="")
    parser.add_argument("--additional-notes", default="")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-shots", type=int, default=5)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--num-inference-steps", type=int, default=8)
    parser.add_argument("--guidance-scale", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-key", default="")
    parser.add_argument("--voice-id", default="Matthew")
    parser.add_argument("--voice-engine", default="neural")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_hq_commercial(
        settings=get_settings(),
        project_id=args.project_id,
        product_image_key=args.product_image_key,
        presenter_image_key=args.presenter_image_key,
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
        max_shots=args.max_shots,
        width=args.width,
        height=args.height,
        num_inference_steps=args.num_inference_steps,
        guidance_scale=args.guidance_scale,
        seed=args.seed,
        output_key=args.output_key,
        voice_id=args.voice_id,
        voice_engine=args.voice_engine,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

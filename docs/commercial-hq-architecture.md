# Commercial HQ Architecture

This path lives separately from the current LTX-only runner and is designed for higher-quality stitched commercials.

## Goal

Use different engines for different shot types:

- `InfiniteTalk` for speaking presenter shots
- `Seedance 1.5 Pro I2V` for product beauty shots and packshots

## Flow

1. `Bedrock` writes the commercial script and shot list.
2. The router assigns each shot:
   - `talking_presenter` -> `InfiniteTalk`
   - `hero_product`, `benefit_cutaway`, `endcard` -> `Seedance`
3. `Polly` generates per-shot speech audio for presenter shots.
4. `InfiniteTalk` uses:
   - presenter image URL
   - speech audio URL
   - presenter motion prompt
5. `Seedance` generates product beauty shots from the product image.
6. `FFmpeg` stitches the final commercial and preserves shot audio.

## Entry point

- [run_hq_commercial.py](D:\openCLI\text 2 video\scripts\run_hq_commercial.py)

## Package

- [commercial_hq](D:\openCLI\text 2 video\packages\python\text2video\commercial_hq)

## Required inputs

- product image S3 key
- presenter image S3 key
- `RUNPOD_LTX_INFERENCE_BASE_URL`
- `RUNPOD_API_KEY`
- AWS credentials for S3 and Polly

## Current tradeoff

This path improves presenter quality and lip-sync control while using Seedance for better-looking product hero shots. Later, `Kling` can be added as another product-shot engine without changing the router shape.

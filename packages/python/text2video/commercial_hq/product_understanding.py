from __future__ import annotations

import json
from pathlib import Path

import httpx

from text2video.aws.s3 import S3Storage
from text2video.aws.session import build_boto3_session
from text2video.bedrock.planner import ShotPlanner
from text2video.config import Settings, get_runtime_path


PRODUCT_UNDERSTANDING_SYSTEM_PROMPT = """You are a product packshot analyst for an AI commercial generation pipeline.

Return valid JSON only with these keys:
- detected_brand
- detected_product_name
- detected_category
- visible_pack_text
- visible_claims
- packaging_colors
- visual_summary
- recommended_key_benefits
- audience_hints
- warnings

Rules:
- Read only what is actually visible or strongly implied by the image and the supplied user hints.
- Do not invent unsupported medical, scientific, or regulatory claims.
- Keep visible_pack_text, visible_claims, packaging_colors, recommended_key_benefits, audience_hints, and warnings as arrays of short strings.
- If a field is uncertain, return the best concise guess and mention the uncertainty in warnings.
- No markdown.
- No explanation outside JSON."""


def analyze_product_image(
    *,
    settings: Settings,
    project_id: str,
    product_image_key: str,
    user_product_name: str,
    user_product_category: str,
) -> dict:
    storage = S3Storage(settings)
    source_path = download_product_image(
        settings=settings,
        storage=storage,
        project_id=project_id,
        product_image_key=product_image_key,
    )
    image_format = detect_image_format(source_path)
    bedrock = build_boto3_session(settings).client(
        "bedrock-runtime",
        region_name=settings.bedrock_region,
    )
    user_hint_lines = [
        f"User product name hint: {user_product_name or 'Not supplied'}",
        f"User product category hint: {user_product_category or 'Not supplied'}",
        f"S3 product image key: {product_image_key}",
        "",
        "Extract the product facts from the attached image and user hints.",
    ]
    try:
        response = bedrock.converse(
            modelId=settings.bedrock_model_id,
            inferenceConfig={
                "temperature": 0.1,
                "maxTokens": min(settings.bedrock_max_tokens, 1500),
            },
            system=[{"text": PRODUCT_UNDERSTANDING_SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "\n".join(user_hint_lines)},
                        {
                            "image": {
                                "format": image_format,
                                "source": {"bytes": source_path.read_bytes()},
                            }
                        },
                    ],
                }
            ],
        )
        content = response["output"]["message"]["content"][0]["text"]
        payload = ShotPlanner._parse_json_response(content)
        normalized = normalize_product_analysis(payload)
    except Exception as exc:
        normalized = fallback_product_analysis(
            settings=settings,
            user_product_name=user_product_name,
            user_product_category=user_product_category,
            product_image_key=product_image_key,
            source_path=source_path,
            error=exc,
        )

    analysis_path = get_runtime_path(settings, "analysis", project_id)
    analysis_path.mkdir(parents=True, exist_ok=True)
    (analysis_path / "product-understanding.json").write_text(
        json.dumps(normalized, indent=2),
        encoding="utf-8",
    )
    return normalized


def fallback_product_analysis(
    *,
    settings: Settings,
    user_product_name: str,
    user_product_category: str,
    product_image_key: str,
    source_path: Path,
    error: Exception,
) -> dict:
    name = (user_product_name or "").strip() or Path(product_image_key).stem.replace("-", " ").strip()
    category = (user_product_category or "").strip() or "digital product"
    visual_summary = (
        f"Reference asset {source_path.name} is available locally for the commercial pipeline. "
        "Automated image analysis fell back from Bedrock, so user-supplied brief fields are treated as the source of truth."
    )
    base = {
        "detected_brand": name,
        "detected_product_name": name,
        "detected_category": category,
        "visible_pack_text": [name] if name else [],
        "visible_claims": [],
        "packaging_colors": [],
        "visual_summary": visual_summary,
        "recommended_key_benefits": [],
        "audience_hints": [],
        "warnings": [f"Fallback analysis used because Bedrock image analysis was unavailable: {type(error).__name__}"],
    }

    if settings.openrouter_api_key:
        try:
            payload = openrouter_chat_json(
                settings=settings,
                model="openai/gpt-4.1-mini",
                system_prompt=PRODUCT_UNDERSTANDING_SYSTEM_PROMPT,
                user_payload={
                    "user_product_name": user_product_name,
                    "user_product_category": user_product_category,
                    "product_image_key": product_image_key,
                    "local_asset_filename": source_path.name,
                    "instruction": (
                        "Image bytes are unavailable in fallback mode. Use the user hints as the source of truth, "
                        "keep unsupported fields conservative, and note uncertainty in warnings."
                    ),
                },
            )
            enriched = normalize_product_analysis(payload)
            enriched["warnings"] = dedupe_strings(
                [*enriched.get("warnings", []), *base["warnings"]],
                limit=6,
            )
            return enriched
        except Exception:
            pass

    return base


def openrouter_chat_json(*, settings: Settings, model: str, system_prompt: str, user_payload: dict) -> dict:
    response = httpx.post(
        f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000/openrouter",
            "X-Title": "Mercury Studio HQ Fallback",
        },
        json={
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2)},
            ],
        },
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    return json.loads(payload["choices"][0]["message"]["content"])


def download_product_image(
    *,
    settings: Settings,
    storage: S3Storage,
    project_id: str,
    product_image_key: str,
) -> Path:
    suffix = Path(product_image_key).suffix or ".png"
    product_dir = get_runtime_path(settings, "analysis", project_id)
    product_dir.mkdir(parents=True, exist_ok=True)
    target_path = product_dir / f"source-product{suffix}"
    if target_path.exists():
        return target_path
    storage.download_file(product_image_key, str(target_path))
    return target_path


def detect_image_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "jpg":
        return "jpeg"
    if suffix in {"jpeg", "png", "webp", "gif"}:
        return suffix
    return "png"


def normalize_product_analysis(payload: dict) -> dict:
    return {
        "detected_brand": str(payload.get("detected_brand") or "").strip(),
        "detected_product_name": str(payload.get("detected_product_name") or "").strip(),
        "detected_category": str(payload.get("detected_category") or "").strip(),
        "visible_pack_text": normalize_string_list(payload.get("visible_pack_text")),
        "visible_claims": normalize_string_list(payload.get("visible_claims")),
        "packaging_colors": normalize_string_list(payload.get("packaging_colors")),
        "visual_summary": normalize_summary(payload.get("visual_summary")),
        "recommended_key_benefits": normalize_string_list(payload.get("recommended_key_benefits")),
        "audience_hints": normalize_string_list(payload.get("audience_hints")),
        "warnings": normalize_string_list(payload.get("warnings")),
    }


def normalize_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    return [str(item).strip() for item in items if str(item).strip()]


def normalize_summary(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def enrich_product_brief(product_brief: dict, analysis: dict) -> dict:
    enriched = dict(product_brief)
    detected_name = analysis.get("detected_product_name", "")
    detected_brand = analysis.get("detected_brand", "")
    detected_category = analysis.get("detected_category", "")
    visible_claims = analysis.get("visible_claims", [])
    visible_pack_text = analysis.get("visible_pack_text", [])
    recommended_benefits = analysis.get("recommended_key_benefits", [])
    audience_hints = analysis.get("audience_hints", [])
    packaging_colors = analysis.get("packaging_colors", [])
    visual_summary = analysis.get("visual_summary", "")

    if not enriched.get("product_name"):
        enriched["product_name"] = detected_name or detected_brand or "Premium product"
    elif detected_brand and detected_brand.lower() not in enriched["product_name"].lower():
        enriched["product_name"] = f"{detected_brand} {enriched['product_name']}".strip()

    if not enriched.get("product_category"):
        enriched["product_category"] = detected_category or "consumer packaged good"

    if not enriched.get("product_description"):
        description_parts = [part for part in [visual_summary] if part]
        if packaging_colors:
            description_parts.append(f"Packaging colors: {', '.join(packaging_colors)}.")
        if visible_pack_text:
            description_parts.append(f"Visible pack text: {', '.join(visible_pack_text[:5])}.")
        enriched["product_description"] = " ".join(description_parts).strip()

    existing_benefits = [item.strip() for item in enriched.get("key_benefits", []) if item.strip()]
    if not existing_benefits:
        existing_benefits = [*visible_claims, *recommended_benefits]
    enriched["key_benefits"] = dedupe_strings(existing_benefits, limit=6)

    if not enriched.get("target_audience") and audience_hints:
        enriched["target_audience"] = ", ".join(audience_hints[:3])

    enriched["product_analysis"] = {
        **analysis,
        "visible_pack_text": visible_pack_text,
        "visible_claims": visible_claims,
        "packaging_colors": packaging_colors,
        "audience_hints": audience_hints,
        "recommended_key_benefits": recommended_benefits,
    }
    return enriched


def dedupe_strings(items: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = " ".join(str(item).split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result

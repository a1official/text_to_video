import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";

const API_BASE = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions";

type OpenRouterCommercialRequest = {
  project_id: string;
  product_image_key: string;
  product_name: string;
  product_category: string;
  prompt: string;
  model: string;
  brief_mode?: "quick" | "detailed";
  product_description?: string;
  target_audience?: string;
  key_benefits?: string[];
  brand_tone?: string;
  call_to_action?: string;
  additional_notes?: string;
};

const SYSTEM_PROMPT = `You are a world-class commercial strategist and creative director.

Return valid JSON only with these keys:
- concept
- campaign_angle
- hook
- voiceover_script
- supers
- music_direction
- visual_language
- shots
- model_notes

Rules:
- Build a premium cinematic commercial for the supplied product brief.
- Use the product brief and extracted image understanding as the source of truth.
- Keep the product visually consistent with the uploaded packshot.
- Write in English only.
- Use exactly 5 shots.
- Use exactly 2 presenter shots and 3 product or benefit shots.
- Each shot must include:
  - shot_id
  - shot_type
  - purpose
  - duration_sec
  - prompt
  - camera
  - motion
  - voiceover_line
  - on_screen_text
- Make the ad feel premium, cohesive, cinematic, and commercially believable.
- Do not invent unsupported claims.
- Avoid repetition across the 5 shots.
- No markdown.
- No explanation outside JSON.`;

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as OpenRouterCommercialRequest;
    const apiKey = resolveOpenRouterKey();

    if (!apiKey) {
      return NextResponse.json(
        { error: "OPENROUTER_API_KEY is not configured for the studio server." },
        { status: 500 },
      );
    }

    const analysisResponse = await fetch(`${API_BASE}/commercials/hq/analyze-product`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: body.project_id,
        product_image_key: body.product_image_key,
        product_name: body.product_name,
        product_category: body.product_category,
      }),
      cache: "no-store",
    });

    const analysisPayloadText = await analysisResponse.text();
    if (!analysisResponse.ok) {
      return new NextResponse(analysisPayloadText, {
        status: analysisResponse.status,
        headers: {
          "Content-Type": analysisResponse.headers.get("Content-Type") ?? "application/json",
        },
      });
    }

    const analysisPayload = JSON.parse(analysisPayloadText) as {
      product_analysis: Record<string, unknown>;
      product_brief: Record<string, unknown>;
    };

    const promptEnvelope = {
      project_id: body.project_id,
      user_prompt: body.prompt,
      brief_mode: body.brief_mode ?? "quick",
      product_name: body.product_name,
      product_category: body.product_category,
      product_description: body.product_description ?? "",
      target_audience: body.target_audience ?? "",
      key_benefits: body.key_benefits ?? [],
      brand_tone: body.brand_tone ?? "Premium, trustworthy, English-language commercial",
      call_to_action: body.call_to_action ?? "",
      additional_notes: body.additional_notes ?? "",
      extracted_product_brief: analysisPayload.product_brief,
      extracted_product_analysis: analysisPayload.product_analysis,
    };

    const openRouterResponse = await fetch(OPENROUTER_BASE_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000/openrouter",
        "X-Title": "Mercury Studio OpenRouter Lab",
      },
      body: JSON.stringify({
        model: body.model,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: JSON.stringify(promptEnvelope, null, 2) },
        ],
      }),
      cache: "no-store",
    });

    const openRouterPayload = await openRouterResponse.json();
    if (!openRouterResponse.ok) {
      return NextResponse.json(openRouterPayload, { status: openRouterResponse.status });
    }

    const content = openRouterPayload?.choices?.[0]?.message?.content;
    const parsed = typeof content === "string" ? JSON.parse(content) : content;

    return NextResponse.json({
      model: body.model,
      project_id: body.project_id,
      product_image_key: body.product_image_key,
      product_analysis: analysisPayload.product_analysis,
      product_brief: analysisPayload.product_brief,
      commercial_package: parsed,
      usage: openRouterPayload.usage ?? null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

function resolveOpenRouterKey(): string {
  if (process.env.OPENROUTER_API_KEY) {
    return process.env.OPENROUTER_API_KEY;
  }

  const envCandidates = [
    path.resolve(process.cwd(), ".env"),
    path.resolve(process.cwd(), "..", ".env"),
    path.resolve(process.cwd(), "..", "..", ".env"),
    path.resolve(process.cwd(), "..", "..", "..", ".env"),
  ];

  for (const candidate of envCandidates) {
    if (!fs.existsSync(candidate)) {
      continue;
    }
    const match = fs
      .readFileSync(candidate, "utf-8")
      .split(/\r?\n/)
      .find((line) => line.startsWith("OPENROUTER_API_KEY="));
    if (match) {
      return match.split("=", 2)[1].trim();
    }
  }

  return "";
}

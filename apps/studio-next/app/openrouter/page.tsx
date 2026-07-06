"use client";

import Link from "next/link";
import { ChangeEvent, DragEvent, useMemo, useState } from "react";

import styles from "./page.module.css";

const modelOptions = [
  "openai/gpt-4.1-mini",
  "openai/gpt-4.1",
  "anthropic/claude-3.7-sonnet",
  "google/gemini-2.5-pro-preview",
  "meta-llama/llama-4-maverick",
];

const defaultPrompt =
  "Design a premium cinematic commercial with a clear hook, a memorable product story arc, confident presenter moments, and polished hero product language.";

type OpenRouterResponse = {
  model: string;
  project_id: string;
  product_image_key: string;
  product_analysis: Record<string, unknown>;
  product_brief: Record<string, unknown>;
  commercial_package: Record<string, unknown>;
  usage?: Record<string, unknown> | null;
};

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(JSON.stringify(payload));
  }
  return payload as T;
}

function generateProjectId() {
  return `openrouter-${Math.random().toString(36).slice(2, 10)}`;
}

export default function OpenRouterPage() {
  const [projectId, setProjectId] = useState(generateProjectId);
  const [model, setModel] = useState(modelOptions[0]);
  const [productName, setProductName] = useState("");
  const [productCategory, setProductCategory] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [keyBenefitsText, setKeyBenefitsText] = useState("");
  const [brandTone, setBrandTone] = useState("Premium, cinematic, trustworthy, product-led");
  const [callToAction, setCallToAction] = useState("");
  const [additionalNotes, setAdditionalNotes] = useState("");
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<OpenRouterResponse | null>(null);

  const prettyResult = useMemo(
    () => (result ? JSON.stringify(result, null, 2) : "No OpenRouter commercial package yet."),
    [result],
  );

  function handleFile(nextFile: File | null) {
    setFile(nextFile);
    setResult(null);
    setError("");
    if (!nextFile) {
      setPreviewUrl("");
      return;
    }
    const objectUrl = URL.createObjectURL(nextFile);
    setPreviewUrl(objectUrl);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    handleFile(event.dataTransfer.files?.[0] ?? null);
  }

  async function runOpenRouter() {
    if (!file) {
      setError("Upload a product image before prompting the OpenRouter lab.");
      return;
    }
    if (!productName.trim() || !productCategory.trim()) {
      setError("Product name and product category are both required.");
      return;
    }

    setIsSubmitting(true);
    setError("");
    setResult(null);

    try {
      const signedUpload = await postJson<{ key: string; url: string }>("/api/upload", {
        project_id: projectId,
        filename: file.name,
        prefix: "uploads",
        expires_in: 600,
      });

      const uploadResponse = await fetch(signedUpload.url, {
        method: "PUT",
        headers: { "Content-Type": file.type || "image/png" },
        body: file,
      });
      if (!uploadResponse.ok) {
        throw new Error(`Upload failed with status ${uploadResponse.status}`);
      }

      const payload = await postJson<OpenRouterResponse>("/api/openrouter/commercial", {
        project_id: projectId,
        product_image_key: signedUpload.key,
        product_name: productName,
        product_category: productCategory,
        product_description: productDescription,
        target_audience: targetAudience,
        key_benefits: keyBenefitsText
          .split(/\r?\n|,/)
          .map((value) => value.trim())
          .filter(Boolean),
        brand_tone: brandTone,
        call_to_action: callToAction,
        additional_notes: additionalNotes,
        prompt,
        model,
        brief_mode: productDescription || targetAudience || keyBenefitsText || callToAction || additionalNotes ? "detailed" : "quick",
      });

      setResult(payload);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : String(caughtError);
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.topbar}>
          <Link href="/" className={styles.backLink}>
            Back To Mercury Studio
          </Link>
          <div className={styles.routeBadge}>/openrouter</div>
        </div>

        <section className={styles.masthead}>
          <div className={styles.heroPanel}>
            <div className={styles.heroInner}>
              <div className={styles.heroStack}>
                <p className={styles.eyebrow}>OpenRouter Commercial Lab</p>
                <h1 className={styles.title}>Use alternate frontier models to generate commercial packages.</h1>
                <p className={styles.subtitle}>
                  This route does not render video directly. It uploads the product packshot, runs the product-understanding
                  layer, and then asks a selected OpenRouter model to return a cinematic commercial concept, voiceover,
                  supers, and a 5-shot plan you can later route into the generation pipeline.
                </p>
              </div>
            </div>
          </div>

          <div className={styles.card}>
            <div className={styles.cardInner}>
              <div className={styles.stack}>
                <div className={styles.field}>
                  <span className={styles.label}>OpenRouter Model</span>
                  <select className={styles.select} value={model} onChange={(event) => setModel(event.target.value)}>
                    {modelOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </div>
                <p className={styles.helper}>
                  The model receives the structured product brief plus image-derived packaging facts, so this page is useful
                  for testing alternate strategic brains before spending on full video generation.
                </p>
                <div className={styles.chipRow}>
                  <span className={styles.chip}>OpenRouter chat models</span>
                  <span className={styles.chip}>Product brief enrichment</span>
                  <span className={styles.chip}>Commercial package JSON</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className={styles.heroGrid}>
          <section className={styles.card}>
            <div className={styles.cardInner}>
              <div className={styles.inputGrid}>
                <label
                  className={`${styles.dropzone} ${styles.wide} ${isDragging ? styles.dropzoneActive : ""}`}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={onDrop}
                >
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    onChange={(event: ChangeEvent<HTMLInputElement>) => handleFile(event.target.files?.[0] ?? null)}
                  />
                  <div className={styles.dropzoneTitle}>Drop the product packshot here</div>
                  <div className={styles.dropzoneHint}>
                    The image is uploaded to S3, analyzed for packaging facts, and then folded into the OpenRouter request.
                  </div>
                  <div className={styles.chipRow}>
                    <span className={styles.chip}>{file ? file.name : "PNG / JPG / WEBP"}</span>
                    <span className={styles.chip}>{file ? `${Math.round(file.size / 1024)} KB` : "Front packshot recommended"}</span>
                  </div>
                </label>

                <div className={`${styles.wide} ${styles.previewFrame}`}>
                  {previewUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={previewUrl} alt="Product preview" className={styles.previewImage} />
                  ) : (
                    <div className={styles.placeholder}>Your uploaded product still will appear here.</div>
                  )}
                </div>

                <label className={styles.field}>
                  <span className={styles.label}>Project ID</span>
                  <input className={styles.input} value={projectId} onChange={(event) => setProjectId(event.target.value)} />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Product Name</span>
                  <input
                    className={styles.input}
                    value={productName}
                    placeholder="Good Day Butter Cookies"
                    onChange={(event) => setProductName(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Product Category</span>
                  <input
                    className={styles.input}
                    value={productCategory}
                    placeholder="Biscuits"
                    onChange={(event) => setProductCategory(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Target Audience</span>
                  <input
                    className={styles.input}
                    value={targetAudience}
                    placeholder="Tea-time snack lovers, families, urban shoppers"
                    onChange={(event) => setTargetAudience(event.target.value)}
                  />
                </label>

                <label className={`${styles.field} ${styles.wide}`}>
                  <span className={styles.label}>Commercial Prompt</span>
                  <textarea className={styles.textarea} value={prompt} onChange={(event) => setPrompt(event.target.value)} />
                </label>

                <label className={`${styles.field} ${styles.wide}`}>
                  <span className={styles.label}>Product Description</span>
                  <textarea
                    className={styles.textarea}
                    value={productDescription}
                    placeholder="Buttery, crunchy biscuit with premium everyday indulgence positioning."
                    onChange={(event) => setProductDescription(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Key Benefits</span>
                  <textarea
                    className={styles.textarea}
                    value={keyBenefitsText}
                    placeholder={"Buttery taste\nCrunchy texture\nTea-time indulgence"}
                    onChange={(event) => setKeyBenefitsText(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Brand Tone</span>
                  <input
                    className={styles.input}
                    value={brandTone}
                    onChange={(event) => setBrandTone(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Call To Action</span>
                  <input
                    className={styles.input}
                    value={callToAction}
                    placeholder="Bring home the golden crunch"
                    onChange={(event) => setCallToAction(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Additional Notes</span>
                  <input
                    className={styles.input}
                    value={additionalNotes}
                    placeholder="Keep it premium, festive, and family-safe."
                    onChange={(event) => setAdditionalNotes(event.target.value)}
                  />
                </label>
              </div>

              <div className={styles.buttonRow}>
                <button type="button" className={styles.primaryButton} onClick={runOpenRouter} disabled={isSubmitting}>
                  {isSubmitting ? "Generating Commercial Package..." : "Generate With OpenRouter"}
                </button>
                <button
                  type="button"
                  className={styles.ghostButton}
                  onClick={() => {
                    setProjectId(generateProjectId());
                    setModel(modelOptions[0]);
                    setProductName("");
                    setProductCategory("");
                    setProductDescription("");
                    setTargetAudience("");
                    setKeyBenefitsText("");
                    setBrandTone("Premium, cinematic, trustworthy, product-led");
                    setCallToAction("");
                    setAdditionalNotes("");
                    setPrompt(defaultPrompt);
                    handleFile(null);
                    setError("");
                    setResult(null);
                  }}
                  disabled={isSubmitting}
                >
                  Reset Lab
                </button>
              </div>

              {error ? <div className={styles.error}>{error}</div> : null}
            </div>
          </section>

          <aside className={styles.card}>
            <div className={styles.cardInner}>
              <div className={styles.stack}>
                <div>
                  <p className={styles.eyebrow}>What This Produces</p>
                  <p className={styles.helper}>
                    A structured creative package you can compare across OpenRouter models before deciding what to send into
                    the video pipeline.
                  </p>
                </div>
                <div className={styles.chipRow}>
                  <span className={styles.chip}>Concept</span>
                  <span className={styles.chip}>Hook</span>
                  <span className={styles.chip}>Voiceover Script</span>
                  <span className={styles.chip}>Supers</span>
                  <span className={styles.chip}>5-Shot Plan</span>
                </div>
              </div>
            </div>
          </aside>
        </section>

        <section className={styles.resultsGrid}>
          <section className={styles.resultsCard}>
            <div className={styles.resultsInner}>
              <h2 className={styles.resultTitle}>Commercial Package</h2>
              <div className={styles.statList}>
                <div className={styles.statItem}>
                  <div className={styles.statLabel}>Model</div>
                  <div className={styles.statValue}>{result?.model ?? model}</div>
                </div>
                <div className={styles.statItem}>
                  <div className={styles.statLabel}>Concept</div>
                  <div className={styles.statValue}>
                    {(result?.commercial_package?.concept as string | undefined) ?? "Concept will appear after generation."}
                  </div>
                </div>
                <div className={styles.statItem}>
                  <div className={styles.statLabel}>Hook</div>
                  <div className={styles.statValue}>
                    {(result?.commercial_package?.hook as string | undefined) ?? "Hook will appear here."}
                  </div>
                </div>
                <div className={styles.statItem}>
                  <div className={styles.statLabel}>Voiceover Script</div>
                  <div className={styles.statValue}>
                    {(result?.commercial_package?.voiceover_script as string | undefined) ?? "Voiceover script will appear here."}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className={styles.resultsCard}>
            <div className={styles.resultsInner}>
              <h2 className={styles.resultTitle}>Raw JSON</h2>
              <pre className={styles.jsonBlock}>{prettyResult}</pre>
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}

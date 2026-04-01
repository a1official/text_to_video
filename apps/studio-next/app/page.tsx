"use client";

import { ChangeEvent, DragEvent, useMemo, useState } from "react";
import styles from "./page.module.css";

type StepState = "idle" | "active" | "done" | "error";
type BriefMode = "quick" | "detailed";

type CommercialResponse = {
  project_id: string;
  summary: string;
  concept: string;
  voiceover_script: string;
  stitched_output_key: string;
  stitched_output_uri: string;
  stitched_local_path: string;
  shots: Array<Record<string, unknown>>;
  segment_debug: Array<Record<string, unknown>>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const defaultPrompt =
  "Create an English premium commercial. Show a confident presenter speaking naturally to camera, intercut with refined luxury product hero shots, label visibility, and a polished closing packshot.";

const initialSteps = [
  {
    key: "upload",
    name: "Asset Intake",
    description: "Upload the product still and mint a signed S3 object key for the commercial run.",
  },
  {
    key: "trigger",
    name: "Pipeline Trigger",
    description: "Send the brief to the commercial HQ pipeline so Bedrock, Nano Banana, InfiniteTalk, and Seedance can run.",
  },
  {
    key: "deliver",
    name: "Delivery",
    description: "Surface the final stitched commercial URI, concept, and the underlying shot plan.",
  },
] as const;

function generateProjectId() {
  return `mercury-${Math.random().toString(36).slice(2, 10)}`;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(JSON.stringify(data));
  }
  return data as T;
}

export default function Page() {
  const [projectId, setProjectId] = useState(generateProjectId);
  const [briefMode, setBriefMode] = useState<BriefMode>("quick");
  const [productName, setProductName] = useState("");
  const [productCategory, setProductCategory] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [keyBenefitsText, setKeyBenefitsText] = useState("");
  const [brandTone, setBrandTone] = useState("Premium, trustworthy, English-language commercial");
  const [callToAction, setCallToAction] = useState("");
  const [additionalNotes, setAdditionalNotes] = useState("");
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [voiceId, setVoiceId] = useState("Matthew");
  const [maxShots, setMaxShots] = useState(5);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CommercialResponse | null>(null);
  const [stepState, setStepState] = useState<Record<string, StepState>>({
    upload: "idle",
    trigger: "idle",
    deliver: "idle",
  });

  const prettyResult = useMemo(
    () => (result ? JSON.stringify(result, null, 2) : "No commercial generated yet."),
    [result],
  );

  function updateStep(key: string, state: StepState) {
    setStepState((previous) => ({ ...previous, [key]: state }));
  }

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
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    handleFile(nextFile);
  }

  async function runPipeline() {
    if (!file) {
      setError("Add a product image before triggering the commercial pipeline.");
      return;
    }
    if (!productName.trim() || !productCategory.trim()) {
      setError("Fill in both the product name and product category before triggering the commercial pipeline.");
      return;
    }

    setIsSubmitting(true);
    setError("");
    setResult(null);
    setStepState({ upload: "active", trigger: "idle", deliver: "idle" });

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

      updateStep("upload", "done");
      updateStep("trigger", "active");

      const commercial = await postJson<CommercialResponse>("/api/commercial", {
        project_id: projectId,
        product_image_key: signedUpload.key,
        brief_mode: briefMode,
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
        max_shots: maxShots,
        voice_id: voiceId,
      });

      updateStep("trigger", "done");
      updateStep("deliver", "done");
      setResult(commercial);
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : String(caughtError);
      setError(message);
      setStepState((current) =>
        Object.fromEntries(
          Object.entries(current).map(([key, value]) => [key, value === "done" ? "done" : "error"]),
        ) as Record<string, StepState>,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <section className={styles.masthead}>
          <div className={styles.brand}>
            <p className={styles.kicker}>Mercury Studio / Commercial OS</p>
            <h1 className={styles.title}>Turn one product still into an English ad film.</h1>
            <p className={styles.subtitle}>
              Upload the packshot, shape the brief, and let the commercial pipeline orchestrate
              Bedrock planning, Nano Banana presenter generation, InfiniteTalk speaking moments,
              Seedance hero shots, and final FFmpeg assembly. The selected voice now determines
              whether the spokesperson is generated as male or female.
            </p>
          </div>

          <div className={styles.badgeCluster}>
            <div className={styles.signalCard}>
              <div className={styles.signalLabel}>Pipeline</div>
              <div className={styles.signalValue}>Serverless-Only</div>
              <div className={styles.signalHint}>
                Nano Banana 2 Edit + InfiniteTalk + Seedance 1.5 Pro, all routed behind one
                cinematic intake surface.
              </div>
            </div>
            <div className={styles.readout}>
              <div className={styles.panelInner}>
                <div className={styles.signalLabel}>Default Voice</div>
                <div className={styles.signalValue}>{voiceId}</div>
                <div className={styles.signalHint}>Exact English copy is synthesized before speaking shots are animated.</div>
              </div>
            </div>
          </div>
        </section>

        <section className={styles.grid}>
          <section className={styles.panel}>
            <div className={styles.panelInner}>
              <h2 className={styles.panelTitle}>Campaign Intake</h2>
              <p className={styles.panelIntro}>
                Feed the system a product image and a direction. The interface handles upload,
                trigger, and delivery without making you babysit individual model calls.
              </p>

              <div className={styles.formGrid}>
                <label
                  className={`${styles.wideField} ${styles.dropzone} ${isDragging ? styles.dropzoneActive : ""}`}
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
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      handleFile(event.target.files?.[0] ?? null)
                    }
                  />
                  <div className={styles.dropzoneTitle}>Drop the product image here</div>
                  <div className={styles.dropzoneHint}>
                    Best results come from a clean packshot or front-facing product still. The
                    backend will preserve this identity across the commercial.
                  </div>
                  <div className={styles.dropzoneMeta}>
                    <span className={styles.chip}>{file ? file.name : "PNG / JPG / WEBP"}</span>
                    <span className={styles.chip}>{file ? `${Math.round(file.size / 1024)} KB` : "Single hero still"}</span>
                  </div>
                </label>

                <div className={styles.wideField}>
                  <span className={styles.label}>Product Preview</span>
                  <div className={styles.previewWrap}>
                    <div className={styles.previewFrame}>
                      {previewUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={previewUrl} alt="Uploaded product preview" className={styles.previewImage} />
                      ) : (
                        <div className={styles.previewPlaceholder}>
                          The uploaded packshot appears here before the pipeline is triggered.
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <label className={styles.field}>
                  <span className={styles.label}>Project ID</span>
                  <input
                    className={styles.input}
                    value={projectId}
                    onChange={(event) => setProjectId(event.target.value)}
                  />
                </label>

                <div className={styles.wideField}>
                  <span className={styles.label}>Brief Mode</span>
                  <div className={styles.modeSwitch}>
                    <button
                      type="button"
                      className={`${styles.modeButton} ${briefMode === "quick" ? styles.modeButtonActive : ""}`}
                      onClick={() => setBriefMode("quick")}
                    >
                      Quick Brief
                    </button>
                    <button
                      type="button"
                      className={`${styles.modeButton} ${briefMode === "detailed" ? styles.modeButtonActive : ""}`}
                      onClick={() => setBriefMode("detailed")}
                    >
                      Commercial Brief
                    </button>
                  </div>
                  <p className={styles.modeHint}>
                    Quick brief uses product name and category for speed. Commercial brief adds
                    audience, benefits, tone, and CTA for stronger Bedrock scripts.
                  </p>
                </div>

                <label className={styles.field}>
                  <span className={styles.label}>Product Name</span>
                  <input
                    className={styles.input}
                    placeholder="Head & Shoulders Deep Scalp Cleanse"
                    value={productName}
                    onChange={(event) => setProductName(event.target.value)}
                  />
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Product Category</span>
                  <input
                    className={styles.input}
                    placeholder="Shampoo"
                    value={productCategory}
                    onChange={(event) => setProductCategory(event.target.value)}
                  />
                </label>

                {briefMode === "detailed" ? (
                  <>
                    <label className={styles.wideField}>
                      <span className={styles.label}>Product Description</span>
                      <textarea
                        className={styles.textareaCompact}
                        placeholder="Summarize what the product is, what it does, and what makes it distinct."
                        value={productDescription}
                        onChange={(event) => setProductDescription(event.target.value)}
                      />
                    </label>

                    <label className={styles.field}>
                      <span className={styles.label}>Target Audience</span>
                      <input
                        className={styles.input}
                        placeholder="Urban professionals looking for scalp-care confidence"
                        value={targetAudience}
                        onChange={(event) => setTargetAudience(event.target.value)}
                      />
                    </label>

                    <label className={styles.field}>
                      <span className={styles.label}>Brand Tone</span>
                      <input
                        className={styles.input}
                        placeholder="Premium, fresh, trustworthy"
                        value={brandTone}
                        onChange={(event) => setBrandTone(event.target.value)}
                      />
                    </label>

                    <label className={styles.wideField}>
                      <span className={styles.label}>Key Benefits</span>
                      <textarea
                        className={styles.textareaCompact}
                        placeholder={"Deep scalp cleanse\nFresh confidence\nAnti-dandruff care"}
                        value={keyBenefitsText}
                        onChange={(event) => setKeyBenefitsText(event.target.value)}
                      />
                    </label>

                    <label className={styles.field}>
                      <span className={styles.label}>Call To Action</span>
                      <input
                        className={styles.input}
                        placeholder="Upgrade your daily scalp-care routine"
                        value={callToAction}
                        onChange={(event) => setCallToAction(event.target.value)}
                      />
                    </label>

                    <label className={styles.field}>
                      <span className={styles.label}>Additional Notes</span>
                      <input
                        className={styles.input}
                        placeholder="Avoid medical claims. Keep the bottle hero in every shot."
                        value={additionalNotes}
                        onChange={(event) => setAdditionalNotes(event.target.value)}
                      />
                    </label>
                  </>
                ) : null}

                <label className={styles.field}>
                  <span className={styles.label}>Voice</span>
                  <select
                    className={styles.input}
                    value={voiceId}
                    onChange={(event) => setVoiceId(event.target.value)}
                  >
                    <option value="Matthew">Matthew</option>
                    <option value="Joanna">Joanna</option>
                    <option value="Brian">Brian</option>
                    <option value="Amy">Amy</option>
                  </select>
                </label>

                <label className={styles.field}>
                  <span className={styles.label}>Shot Count</span>
                  <input
                    className={styles.input}
                    type="number"
                    min={3}
                    max={7}
                    value={maxShots}
                    onChange={(event) => setMaxShots(Number(event.target.value))}
                  />
                </label>

                <div className={styles.field}>
                  <span className={styles.label}>API Base</span>
                  <input className={styles.input} value={API_BASE} readOnly />
                </div>

                <label className={styles.wideField}>
                  <span className={styles.label}>Commercial Prompt</span>
                  <textarea
                    className={styles.textarea}
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                  />
                </label>
              </div>

              <div className={styles.actionRow}>
                <button
                  type="button"
                  className={styles.primaryButton}
                  onClick={runPipeline}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Generating Commercial..." : "Trigger Pipeline"}
                </button>
                <button
                  type="button"
                  className={styles.ghostButton}
                  onClick={() => {
                    setProjectId(generateProjectId());
                    setBriefMode("quick");
                    setProductName("");
                    setProductCategory("");
                    setProductDescription("");
                    setTargetAudience("");
                    setKeyBenefitsText("");
                    setBrandTone("Premium, trustworthy, English-language commercial");
                    setCallToAction("");
                    setAdditionalNotes("");
                    setPrompt(defaultPrompt);
                    setVoiceId("Matthew");
                    setMaxShots(5);
                    handleFile(null);
                    setError("");
                    setResult(null);
                    setStepState({ upload: "idle", trigger: "idle", deliver: "idle" });
                  }}
                  disabled={isSubmitting}
                >
                  Reset Board
                </button>
              </div>

              {error ? <div className={styles.errorBanner}>{error}</div> : null}
            </div>
          </section>

          <aside className={styles.sideNotes}>
            <section className={styles.noteCard}>
              <h3 className={styles.noteTitle}>Pipeline Ladder</h3>
              <div className={styles.statusGrid}>
                {initialSteps.map((step) => {
                  const state = stepState[step.key];
                  return (
                    <article
                      key={step.key}
                      className={`${styles.stepCard} ${
                        state === "active"
                          ? styles.stateActive
                          : state === "done"
                            ? styles.stateDone
                            : state === "error"
                              ? styles.stateError
                              : styles.stateIdle
                      }`}
                    >
                      <div className={styles.stepTop}>
                        <div className={styles.stepName}>{step.name}</div>
                        <div className={styles.stepState}>{state}</div>
                      </div>
                      <div className={styles.stepDesc}>{step.description}</div>
                    </article>
                  );
                })}
              </div>
            </section>

            <section className={styles.noteCard}>
              <h3 className={styles.noteTitle}>What This Triggers</h3>
              <p className={styles.noteBody}>
                Bedrock now writes the English commercial script from either a fast product brief
                or a richer commercial-grade brief. Nano Banana creates the presenter still,
                InfiniteTalk handles speaking performance, Seedance renders hero product shots, and
                FFmpeg assembles the final cut.
              </p>
              <div className={styles.pillRow}>
                <span className={styles.pill}>Bedrock planning</span>
                <span className={styles.pill}>Nano Banana 2 Edit</span>
                <span className={styles.pill}>InfiniteTalk</span>
                <span className={styles.pill}>Seedance 1.5 Pro</span>
                <span className={styles.pill}>FFmpeg stitch</span>
              </div>
            </section>
          </aside>
        </section>

        <section className={styles.resultsPanel}>
          <div className={styles.resultsCard}>
            <div className={styles.resultsHeader}>
              <div>
                <h2 className={styles.resultsTitle}>Delivery Console</h2>
                <p className={styles.resultsLead}>
                  Final stitched output, concept summary, and raw pipeline response appear here after the commercial completes.
                </p>
              </div>
              <div className={styles.tag}>{result ? "Commercial Ready" : "Awaiting Run"}</div>
            </div>

            <div className={styles.resultList}>
              <div className={styles.resultItem}>
                <div className={styles.resultLabel}>Stitched Output URI</div>
                <div className={styles.resultValue}>{result?.stitched_output_uri ?? "No output yet"}</div>
              </div>
              <div className={styles.resultItem}>
                <div className={styles.resultLabel}>Concept</div>
                <div className={styles.resultValue}>{result?.concept ?? "The concept summary will appear after the run finishes."}</div>
              </div>
              <div className={styles.resultItem}>
                <div className={styles.resultLabel}>Voiceover Script</div>
                <div className={styles.resultValue}>{result?.voiceover_script ?? "No voiceover drafted yet."}</div>
              </div>
            </div>

            <pre className={styles.jsonBlock}>{prettyResult}</pre>
          </div>
        </section>
      </div>
    </main>
  );
}

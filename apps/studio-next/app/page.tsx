"use client";

import { FormEvent, useMemo, useState } from "react";
import styles from "./page.module.css";

type StoryLambdaResponse = {
  function_name: string;
  invoked_at: string;
  request: {
    title: string;
    created_by: string;
    prompt: string;
    voice_id: string;
    language_code: string;
    priority: number;
    image_count: number;
    duration_sec: number;
    approval_required: boolean;
  };
  result: {
    project_id?: string;
    state?: string;
    detail?: string;
    review_state?: string;
    approval_required?: boolean;
    final_output_uri?: string;
    final_download_url?: string;
    jobs?: unknown[];
    outputs?: unknown[];
    [key: string]: unknown;
  };
};

type ReviewShot = {
  shot_id: string;
  sequence_index?: number;
  duration_sec?: number;
  review_status?: string;
  appearance_prompt?: string;
  motion_prompt?: string;
  camera_prompt?: string;
  edit_prompt?: string;
  latest_output_key?: string;
  latest_output_url?: string;
  latest_output_type?: string;
  approved_for_render?: boolean;
};

type ReviewResponse = {
  project_id: string;
  project: Record<string, unknown>;
  review_state?: string;
  approval_required?: boolean;
  shots: ReviewShot[];
  outputs: Array<Record<string, unknown>>;
  detail?: string;
  state?: string;
  final_download_url?: string;
};

type PollResponse = {
  state?: string;
  detail?: string;
  final_output_uri?: string;
  final_download_url?: string;
  job_count?: number;
  manifest_count?: number;
  failed_jobs?: unknown[];
  outputs?: unknown[];
  review_state?: string;
  shots?: ReviewShot[];
  [key: string]: unknown;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : JSON.stringify(data));
  }
  return data as T;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : JSON.stringify(data));
  }
  return data as T;
}

async function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function Page() {
  const [prompt, setPrompt] = useState(
    "A lone traveler reaches the snowbound citadel at dawn and chooses a new future.",
  );
  const [imageCount, setImageCount] = useState(5);
  const [durationSec, setDurationSec] = useState(6);
  const [voiceId, setVoiceId] = useState("Matthew");
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [logs, setLogs] = useState<string[]>(["Ready. Send a prompt and generate review frames first."]);
  const [finalUrl, setFinalUrl] = useState("");
  const [editDrafts, setEditDrafts] = useState<Record<string, string>>({});

  const logText = useMemo(() => logs.join("\n"), [logs]);
  const reviewShots = review?.shots ?? [];
  const readyShots = reviewShots.filter((shot) => shot.latest_output_url);
  const allShotsReady = reviewShots.length > 0 && readyShots.length === reviewShots.length;

  function appendLog(message: string) {
    const stamp = new Date().toLocaleTimeString();
    setLogs((current) => [...current, `[${stamp}] ${message}`]);
  }

  async function refreshReview(id: string) {
    const data = await getJson<ReviewResponse>(`/projects/${id}/review`);
    setReview(data);
    setProjectId(id);
    setEditDrafts((current) => {
      const next = { ...current };
      for (const shot of data.shots) {
        if (next[shot.shot_id] === undefined) {
          next[shot.shot_id] = shot.edit_prompt ?? "";
        }
      }
      return next;
    });
    return data;
  }

  async function pollUntilReviewReady(id: string) {
    appendLog(`Watching project ${id} until the review frames are ready.`);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const data = await refreshReview(id);
      const imageCountReady = data.shots.filter((shot) => shot.latest_output_url).length;
      appendLog(
        `Review state: ${data.review_state ?? "unknown"} | images ready: ${imageCountReady}/${data.shots.length}`,
      );
      if (data.review_state === "awaiting_review" && imageCountReady > 0) {
        appendLog("Images are ready for human review.");
        return;
      }
      await sleep(5000);
    }
    appendLog("Review polling stopped after the time limit.");
  }

  async function pollUntilDone(id: string) {
    appendLog(`Polling project ${id} for final render status.`);
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const poll = await postJson<PollResponse>(`/projects/${id}/poll`, {
        scene_id: "scene001",
        output_prefix: "stitched",
        output_filename: "scene001.mp4",
      });
      appendLog(`Project state: ${poll.state ?? "unknown"}${poll.detail ? ` | ${poll.detail}` : ""}`);

      if (poll.final_download_url) {
        setFinalUrl(poll.final_download_url);
        appendLog("Final video link is ready.");
        return;
      }

      if (poll.state === "failed") {
        appendLog("Pipeline failed. Check the response JSON for details.");
        return;
      }

      await sleep(5000);
    }

    appendLog("Polling stopped after reaching the time limit.");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;

    setLoading(true);
    setFinalUrl("");
    setReview(null);
    setLogs([]);
    setEditDrafts({});

    try {
      appendLog("Submitting prompt to /pipelines/story/lambda.");
      const payload = {
        title: "Lambda Story Launch",
        created_by: "akash",
        prompt,
        voice_id: voiceId,
        language_code: "en-IN",
        priority: 100,
        image_count: imageCount,
        duration_sec: durationSec,
        approval_required: true,
      };

      const data = await postJson<StoryLambdaResponse>("/pipelines/story/lambda", payload);
      setProjectId(data.result.project_id ?? "");
      appendLog(`Lambda invoked: ${data.function_name}`);
      appendLog(`Project ID: ${data.result.project_id ?? "unknown"}`);
      appendLog(`Initial state: ${data.result.review_state ?? data.result.state ?? "queued"}`);
      appendLog("Image generation jobs queued. Waiting for review frames...");

      if (data.result.project_id) {
        await sleep(1500);
        await pollUntilReviewReady(data.result.project_id);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      appendLog(`Error: ${message}`);
    } finally {
      setLoading(false);
    }
  }

  async function regenerateShot(shotId: string) {
    if (!projectId) return;
    const editPrompt = (editDrafts[shotId] ?? "").trim();
    if (!editPrompt) {
      appendLog(`Shot ${shotId}: add an edit prompt before regenerating.`);
      return;
    }
    appendLog(`Shot ${shotId}: regenerating image with edit prompt.`);
    await postJson(`/projects/${projectId}/review/regenerate`, {
      shot_id: shotId,
      edit_prompt: editPrompt,
      priority: 100,
    });
    await refreshReview(projectId);
    appendLog(`Shot ${shotId}: regeneration queued.`);
  }

  async function approveAll() {
    if (!projectId) return;
    if (!allShotsReady) {
      appendLog("Wait until every image is ready before approving the set.");
      return;
    }
    setApproving(true);
    try {
      appendLog("Approval received. Sending approved images to Veo and generating voiceover.");
      const locallyApproved = reviewShots.filter((shot) => shot.approved_for_render);
      const approvedShotIds = (locallyApproved.length > 0 ? locallyApproved : reviewShots.filter((shot) => shot.latest_output_url)).map(
        (shot) => shot.shot_id,
      );
      await postJson(`/projects/${projectId}/review/approve`, {
        approved_shot_ids: approvedShotIds,
        generate_voiceover: true,
        priority: 100,
      });
      appendLog("Veo jobs queued. Watching for the final stitched output.");
      await pollUntilDone(projectId);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      appendLog(`Approval failed: ${message}`);
    } finally {
      setApproving(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.glowA} />
      <div className={styles.glowB} />
      <section className={styles.shell}>
        <header className={styles.hero}>
          <div className={styles.heroCopy}>
            <p className={styles.kicker}>Human-in-the-loop Lambda pipeline</p>
            <h1>Generate the images first. Approve them. Then let Veo take over.</h1>
            <p className={styles.subtitle}>
              This studio asks for the prompt, image count, and per-shot duration up front. Lambda generates the review
              frames with Nano Banana 2, the gallery appears here, and nothing is sent to Veo until you approve it.
            </p>
          </div>

          <div className={styles.statusCard}>
            <div className={styles.statusLabel}>Pipeline state</div>
            <div className={styles.statusValue}>{review?.review_state ?? "idle"}</div>
            <div className={styles.statusMeta}>
              <span>Project: {projectId || "not started"}</span>
              <span>Images ready: {readyShots.length}/{reviewShots.length || imageCount}</span>
            </div>
            <div className={styles.statusMeta}>
              <span>Voice: {voiceId}</span>
              <span>Duration: {durationSec}s per shot</span>
            </div>
          </div>
        </header>

        <section className={styles.controlsGrid}>
          <form className={styles.controlCard} onSubmit={handleSubmit}>
            <div className={styles.cardHeader}>
              <div>
                <p className={styles.cardKicker}>Step 1</p>
                <h2>Define the story request</h2>
              </div>
              <button className={styles.primaryButton} type="submit" disabled={loading}>
                {loading ? "Generating images..." : "Run Lambda"}
              </button>
            </div>

            <label className={styles.field} htmlFor="prompt">
              <span className={styles.label}>Prompt</span>
              <textarea
                id="prompt"
                className={styles.textarea}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="Describe the story, the mood, the look, and the emotional arc."
              />
            </label>

            <div className={styles.formRow}>
              <label className={styles.field}>
                <span className={styles.label}>Image count</span>
                <input
                  className={styles.input}
                  type="number"
                  min={1}
                  max={12}
                  value={imageCount}
                  onChange={(event) => setImageCount(Number(event.target.value) || 1)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Seconds per video</span>
                <input
                  className={styles.input}
                  type="number"
                  min={2}
                  max={12}
                  value={durationSec}
                  onChange={(event) => setDurationSec(Number(event.target.value) || 5)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Voice ID</span>
                <input
                  className={styles.input}
                  value={voiceId}
                  onChange={(event) => setVoiceId(event.target.value)}
                />
              </label>
            </div>
          </form>

          <aside className={styles.logCard}>
            <div className={styles.cardHeader}>
              <div>
                <p className={styles.cardKicker}>Live logs</p>
                <h2>Lambda and review telemetry</h2>
              </div>
            </div>
            <div className={styles.metaLine}>
              <span>API: {API_BASE}</span>
              <span>State: {review?.state ?? review?.review_state ?? "idle"}</span>
            </div>
            <pre className={styles.logBox}>{logText}</pre>
          </aside>
        </section>

        <section className={styles.reviewSection}>
          <div className={styles.reviewHeader}>
            <div>
              <p className={styles.cardKicker}>Step 2</p>
              <h2>Review the generated images before Veo</h2>
              <p className={styles.reviewCopy}>
                Each card shows the exact prompt that produced the frame, plus an edit prompt you can refine before
                regenerating. Only approved shots will move into the video stage.
              </p>
            </div>
            <button className={styles.secondaryButton} type="button" onClick={() => projectId && refreshReview(projectId)}>
              Refresh review
            </button>
          </div>

          <div className={styles.reviewGrid}>
            {reviewShots.length > 0 ? (
              reviewShots.map((shot) => (
                <article className={styles.shotCard} key={shot.shot_id}>
                  <div className={styles.shotMedia}>
                    {shot.latest_output_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img className={styles.shotImage} src={shot.latest_output_url} alt={shot.shot_id} />
                    ) : (
                      <div className={styles.shotPlaceholder}>Awaiting generated image</div>
                    )}
                    <span className={styles.shotBadge}>
                      {shot.review_status ?? "pending_review"} · {shot.duration_sec ?? durationSec}s
                    </span>
                  </div>

                  <div className={styles.shotBody}>
                    <div className={styles.shotTopline}>
                      <strong>{shot.shot_id}</strong>
                      <span>
                        #{shot.sequence_index ?? 0} {shot.approved_for_render ? "Approved" : "Needs review"}
                      </span>
                    </div>

                    <div className={styles.promptBlock}>
                      <span className={styles.promptLabel}>Generated prompt</span>
                      <p>{shot.appearance_prompt || "No prompt available yet."}</p>
                    </div>

                    <label className={styles.field}>
                      <span className={styles.label}>Edit prompt</span>
                      <textarea
                        className={styles.smallTextarea}
                        value={editDrafts[shot.shot_id] ?? ""}
                        onChange={(event) =>
                          setEditDrafts((current) => ({
                            ...current,
                            [shot.shot_id]: event.target.value,
                          }))
                        }
                        placeholder="Add a refinement like: warmer light, stronger subject focus, cleaner background..."
                      />
                    </label>

                    <div className={styles.cardActions}>
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => regenerateShot(shot.shot_id)}
                      >
                        Regenerate
                      </button>
                      <button
                        className={styles.approveButton}
                        type="button"
                        onClick={() => setReview((current) => current ? {
                          ...current,
                          shots: current.shots.map((item) =>
                            item.shot_id === shot.shot_id ? { ...item, approved_for_render: true } : item,
                          ),
                        } : current)}
                      >
                        Mark ready
                      </button>
                    </div>
                  </div>
                </article>
              ))
            ) : (
              <div className={styles.emptyState}>
                <strong>Images will show here once the Lambda jobs finish.</strong>
                <span>
                  After the review frames are ready, you can edit each shot prompt, regenerate, and then approve the set
                  for Veo.
                </span>
              </div>
            )}
          </div>
        </section>

        <footer className={styles.footerBar}>
          <div className={styles.footerMeta}>
            <span>{projectId ? `Project ${projectId}` : "No project launched yet"}</span>
            <span>{reviewShots.length ? `${reviewShots.length} review cards ready` : "Awaiting first run"}</span>
          </div>
          <div className={styles.footerActions}>
            <button className={styles.secondaryButton} type="button" onClick={() => projectId && refreshReview(projectId)}>
              Sync review
            </button>
            <button className={styles.primaryButton} type="button" onClick={approveAll} disabled={approving || !allShotsReady}>
              {approving ? "Sending to Veo..." : "Approve all & render"}
            </button>
          </div>
          {finalUrl ? (
            <a className={styles.finalLink} href={finalUrl} target="_blank" rel="noreferrer">
              Open final video
            </a>
          ) : null}
        </footer>
      </section>
    </main>
  );
}

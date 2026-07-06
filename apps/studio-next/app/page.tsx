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
  };
  result: {
    project_id?: string;
    state?: string;
    detail?: string;
    final_output_uri?: string;
    final_download_url?: string;
    jobs?: unknown[];
    outputs?: unknown[];
    [key: string]: unknown;
  };
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

async function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function Page() {
  const [prompt, setPrompt] = useState(
    "Create a cinematic travel story about a traveler discovering hidden cafes, scenic routes, and authentic local moments.",
  );
  const [loading, setLoading] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [logs, setLogs] = useState<string[]>([
    "Ready. Paste a story prompt and launch the Lambda pipeline.",
  ]);
  const [finalUrl, setFinalUrl] = useState("");
  const logText = useMemo(() => logs.join("\n"), [logs]);

  function appendLog(message: string) {
    const stamp = new Date().toLocaleTimeString();
    setLogs((current) => [...current, `[${stamp}] ${message}`]);
  }

  async function pollUntilDone(id: string) {
    appendLog(`Polling project ${id} for status updates.`);
    for (let i = 0; i < 40; i += 1) {
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

      if (poll.state === "complete") {
        appendLog("Pipeline completed, but no download URL was returned yet.");
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
    setLogs([]);

    try {
      appendLog("Submitting prompt to /pipelines/story/lambda.");
      const payload = {
        title: "Lambda Story Launch",
        created_by: "akash",
        prompt,
        voice_id: "Matthew",
        language_code: "en-IN",
        priority: 100,
      };

      const data = await postJson<StoryLambdaResponse>("/pipelines/story/lambda", payload);
      setProjectId(data.result.project_id ?? "");
      appendLog(`Lambda invoked: ${data.function_name}`);
      appendLog(`Project ID: ${data.result.project_id ?? "unknown"}`);
      appendLog(`Initial state: ${data.result.state ?? "queued"}`);

      const finalDownloadUrl = data.result.final_download_url;
      if (finalDownloadUrl) {
        setFinalUrl(finalDownloadUrl);
        appendLog("Final download URL returned immediately.");
        return;
      }

      if (data.result.project_id) {
        await sleep(2000);
        await pollUntilDone(data.result.project_id);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      appendLog(`Error: ${message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.shell}>
        <header className={styles.header}>
          <p className={styles.kicker}>Lambda Prompt Console</p>
          <h1>One prompt in. One button. Live logs out.</h1>
          <p className={styles.subtitle}>
            Send a story prompt to the Lambda route and watch the orchestration progress in real time.
          </p>
        </header>

        <div className={styles.grid}>
          <form className={styles.panel} onSubmit={handleSubmit}>
            <label className={styles.label} htmlFor="prompt">
              Story prompt
            </label>
            <textarea
              id="prompt"
              className={styles.textarea}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Describe the story you want Lambda to launch..."
            />
            <div className={styles.actions}>
              <button className={styles.primaryButton} type="submit" disabled={loading}>
                {loading ? "Running Lambda..." : "Run Lambda"}
              </button>
            </div>
          </form>

          <aside className={styles.logPanel}>
            <div className={styles.logHeader}>
              <h2>Live Logs</h2>
              {finalUrl ? (
                <a className={styles.link} href={finalUrl} target="_blank" rel="noreferrer">
                  Open final video
                </a>
              ) : null}
            </div>
            <div className={styles.metaLine}>
              <span>API: {API_BASE}</span>
              <span>Project: {projectId || "not started"}</span>
            </div>
            <pre className={styles.logBox}>{logText}</pre>
          </aside>
        </div>
      </section>
    </main>
  );
}

const storyForm = document.getElementById("story-form");
const responseJson = document.getElementById("response-json");
const projectIdEl = document.getElementById("project-id");
const statusLine = document.getElementById("status-line");
const refreshBtn = document.getElementById("refresh-btn");
const copyJsonBtn = document.getElementById("copy-json");

let currentProjectId = "";

function renderJson(value) {
  return JSON.stringify(value, null, 2);
}

function writeOutput(label, data) {
  const payload = typeof data === "string" ? data : renderJson(data);
  responseJson.textContent = `${label}\n\n${payload}`;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail ? JSON.stringify(data.detail) : JSON.stringify(data));
  }
  return data;
}

storyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(storyForm);
  const payload = Object.fromEntries(form.entries());

  statusLine.textContent = "Launching Lambda story...";
  responseJson.textContent = "Submitting prompt to the Lambda route...";

  try {
    const data = await postJson("/pipelines/story/lambda", payload);
    currentProjectId = data?.result?.project_id || data?.result?.project?.project_id || "";
    projectIdEl.textContent = currentProjectId || "Project launched";
    statusLine.textContent = `Lambda invoked at ${new Date(data.invoked_at).toLocaleString()}`;
    writeOutput("Lambda launch response", data);
  } catch (error) {
    statusLine.textContent = "Launch failed.";
    writeOutput("Launch failed", String(error));
  }
});

refreshBtn.addEventListener("click", async () => {
  if (!currentProjectId) {
    writeOutput("Refresh", "Launch a project first so we have a project ID to poll.");
    return;
  }

  statusLine.textContent = "Polling project state...";
  try {
    const result = await postJson(`/projects/${currentProjectId}/poll`, {
      scene_id: "scene001",
      output_prefix: "stitched",
      output_filename: "scene001.mp4",
    });
    statusLine.textContent = `Project state: ${result.state}`;
    writeOutput("Project poll", result);
  } catch (error) {
    statusLine.textContent = "Poll failed.";
    writeOutput("Poll failed", String(error));
  }
});

copyJsonBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(responseJson.textContent);
    copyJsonBtn.textContent = "Copied";
    setTimeout(() => {
      copyJsonBtn.textContent = "Copy JSON";
    }, 1200);
  } catch {
    copyJsonBtn.textContent = "Copy failed";
    setTimeout(() => {
      copyJsonBtn.textContent = "Copy JSON";
    }, 1200);
  }
});

writeOutput("Ready", {
  message: "Type your story prompt above and launch the Lambda pipeline.",
  route: "/pipelines/story/lambda",
});

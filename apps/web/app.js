const consoleEl = document.getElementById("console");
const inspectForm = document.getElementById("inspect-form");
let inspectProjectId = "";
let inspectTarget = "shots";

function writeOutput(label, data) {
  const rendered =
    typeof data === "string" ? data : JSON.stringify(data, null, 2);
  consoleEl.textContent = `${label}\n\n${rendered}`;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(JSON.stringify(data));
  }
  return data;
}

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(JSON.stringify(data));
  }
  return data;
}

document.getElementById("project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await postJson("/projects", Object.fromEntries(form.entries()));
    writeOutput("Project created", data);
  } catch (error) {
    writeOutput("Project creation failed", String(error));
  }
});

document.getElementById("plan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await postJson("/planner/plan", {
      project_id: form.get("project_id"),
      prompt: form.get("prompt"),
      references: [],
    });
    writeOutput("Plan persisted", data);
  } catch (error) {
    writeOutput("Plan failed", String(error));
  }
});

document.getElementById("jobs-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await postJson(`/projects/${form.get("project_id")}/jobs/from-plan`, {
      priority: 100,
      include_continuity: true,
    });
    writeOutput("Jobs queued", data);
  } catch (error) {
    writeOutput("Job queueing failed", String(error));
  }
});

document.getElementById("stitch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await postJson(`/projects/${form.get("project_id")}/stitch-plan`, {
      scene_id: form.get("scene_id"),
      output_prefix: "stitched",
      output_filename: `${form.get("scene_id")}.mp4`,
      priority: 90,
    });
    writeOutput("Stitch manifest created", data);
  } catch (error) {
    writeOutput("Stitch planning failed", String(error));
  }
});

document.getElementById("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const data = await postJson("/assets/signed-upload", {
      project_id: form.get("project_id"),
      filename: form.get("filename"),
      prefix: "uploads",
      expires_in: 300,
    });
    writeOutput("Signed upload generated", data);
  } catch (error) {
    writeOutput("Signed upload failed", String(error));
  }
});

inspectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  inspectProjectId = form.get("project_id");
  await inspectCurrentTarget();
});

document.querySelectorAll("[data-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    inspectTarget = button.dataset.target;
    await inspectCurrentTarget();
  });
});

async function inspectCurrentTarget() {
  if (!inspectProjectId) {
    writeOutput("Inspect", "Enter a project ID first.");
    return;
  }

  const endpoints = {
    shots: `/projects/${inspectProjectId}/shots`,
    jobs: `/projects/${inspectProjectId}/jobs`,
    manifests: `/projects/${inspectProjectId}/manifests`,
    outputs: `/projects/${inspectProjectId}/outputs`,
  };

  try {
    const data = await getJson(endpoints[inspectTarget]);
    writeOutput(`Project ${inspectTarget}`, data);
  } catch (error) {
    writeOutput(`Failed loading ${inspectTarget}`, String(error));
  }
}

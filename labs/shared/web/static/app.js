const MAX_REQUEST_MESSAGES = 80;
const MAX_REQUEST_MESSAGE_CHARACTERS = 50_000;
const RELOAD_ATTEMPTS = 12;
const RELOAD_DELAY_MS = 500;
const DEBUG_LOG_LIMIT = 100;
const WEB_SOURCES_PER_PAGE = window.matchMedia("(max-height: 800px)").matches ? 1 : 2;

const state = {
  stages: [],
  backends: [],
  stage: localStorage.getItem("harness.currentStage") || "lab_01",
  backend: localStorage.getItem("harness.productBackend") || "thin_harness",
  workspaceId: localStorage.getItem("harness.workspaceId") || createId("workspace"),
  sessionId: localStorage.getItem("harness.sessionId") || createId("session"),
  materials: [],
  thinking: false,
  connectionLost: false,
  hasConnected: false,
  loadingApplicationData: false,
  reloadingAgent: false,
  debugLog: [],
  health: null,
  materialSourcePage: 0,
};

localStorage.setItem("harness.workspaceId", state.workspaceId);
localStorage.setItem("harness.sessionId", state.sessionId);

const elements = {
  stageSelect: document.querySelector("#stage-select"),
  backendControl: document.querySelector("#backend-control"),
  backendSelect: document.querySelector("#backend-select"),
  reloadAgent: document.querySelector("#reload-agent"),
  status: document.querySelector("#connection-status"),
  statusLabel: document.querySelector("#connection-label"),
  apiKeyDialog: document.querySelector("#api-key-dialog"),
  apiKeyForm: document.querySelector("#api-key-form"),
  apiKeyInput: document.querySelector("#api-key-input"),
  closeApiKey: document.querySelector("#close-api-key"),
  cancelApiKey: document.querySelector("#cancel-api-key"),
  saveApiKey: document.querySelector("#save-api-key"),
  privacyBanner: document.querySelector("#privacy-banner"),
  dismissPrivacy: document.querySelector("#dismiss-privacy"),
  materialsIntro: document.querySelector("#materials-intro"),
  materialsList: document.querySelector("#materials-list"),
  materialsPagination: document.querySelector("#materials-pagination"),
  materialKind: document.querySelector("#material-kind"),
  materialFile: document.querySelector("#material-file"),
  uploadLabel: document.querySelector("#upload-label"),
  clearMaterials: document.querySelector("#clear-materials"),
  openPaste: document.querySelector("#open-paste"),
  pasteDialog: document.querySelector("#paste-dialog"),
  pasteForm: document.querySelector("#paste-form"),
  pasteName: document.querySelector("#paste-name"),
  pasteText: document.querySelector("#paste-text"),
  cancelPaste: document.querySelector("#cancel-paste"),
  webSourceForm: document.querySelector("#web-source-form"),
  webSourceUrl: document.querySelector("#web-source-url"),
  capabilityCard: document.querySelector("#capability-card"),
  transcript: document.querySelector("#chat-transcript"),
  clearChat: document.querySelector("#clear-chat"),
  runEval: document.querySelector("#run-eval"),
  chatForm: document.querySelector("#chat-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  events: document.querySelector("#inspector-events"),
  stateSummary: document.querySelector("#state-summary"),
  artifacts: document.querySelector("#artifact-links"),
  runId: document.querySelector("#run-id"),
  copyDebugLog: document.querySelector("#copy-debug-log"),
  toast: document.querySelector("#toast"),
};

boot();

async function boot() {
  logDebug("ui", "boot", "started");
  bindDebugEvents();
  bindEvents();
  window.setInterval(pollHealth, 1500);
  if (!localStorage.getItem("harness.privacyAcknowledged")) {
    elements.privacyBanner.hidden = false;
  }
  try {
    await loadHealth();
    await loadApplicationData();
  } catch (error) {
    logDebug("ui", "boot", "failed", { error_kind: error.name });
    setConnection("reloading", "Reconnecting…");
    state.connectionLost = true;
  }
}

function bindDebugEvents() {
  window.addEventListener("error", (event) => {
    logDebug("ui", "window_error", "failed", {
      error_kind: event.error?.name || "Error",
      file: event.filename ? new URL(event.filename, window.location.href).pathname : undefined,
      line: event.lineno || undefined,
      column: event.colno || undefined,
    });
  });
  window.addEventListener("unhandledrejection", (event) => {
    logDebug("ui", "unhandled_promise", "failed", {
      error_kind: event.reason?.name || "PromiseRejection",
    });
  });
}

async function loadApplicationData() {
  if (state.loadingApplicationData) return;
  state.loadingApplicationData = true;
  try {
    await Promise.all([loadStages(), loadBackends()]);
    await loadMaterials();
    await ensureStageDefaults();
    renderTranscript();
  } finally {
    state.loadingApplicationData = false;
  }
}

function bindEvents() {
  elements.dismissPrivacy.addEventListener("click", () => {
    localStorage.setItem("harness.privacyAcknowledged", "true");
    elements.privacyBanner.hidden = true;
  });
  elements.stageSelect.addEventListener("change", async () => {
    state.stage = elements.stageSelect.value;
    localStorage.setItem("harness.currentStage", state.stage);
    renderCapabilityCard();
    renderTranscript();
    clearInspector();
    renderBackendControl();
    renderMaterialControls();
    await loadMaterials();
    await ensureStageDefaults();
  });
  elements.backendSelect.addEventListener("change", () => {
    state.backend = elements.backendSelect.value;
    localStorage.setItem("harness.productBackend", state.backend);
  });
  elements.reloadAgent.addEventListener("click", reloadAgent);
  elements.status.addEventListener("click", openApiKeyDialog);
  elements.closeApiKey.addEventListener("click", closeApiKeyDialog);
  elements.cancelApiKey.addEventListener("click", closeApiKeyDialog);
  elements.apiKeyDialog.addEventListener("close", () => { elements.apiKeyInput.value = ""; });
  elements.apiKeyForm.addEventListener("submit", saveApiKey);
  elements.copyDebugLog.addEventListener("click", copyDebugLog);
  elements.materialFile.addEventListener("change", uploadSelectedFile);
  elements.openPaste.addEventListener("click", () => elements.pasteDialog.showModal());
  elements.cancelPaste.addEventListener("click", () => elements.pasteDialog.close());
  elements.pasteForm.addEventListener("submit", pasteMaterial);
  elements.webSourceForm.addEventListener("submit", addWebSource);
  elements.clearMaterials.addEventListener("click", clearJobWorkspace);
  elements.clearChat.addEventListener("click", () => {
    saveMessages([]);
    renderTranscript();
    clearInspector();
  });
  elements.runEval.addEventListener("click", runEvalSuite);
  elements.chatForm.addEventListener("submit", sendMessage);
  elements.messageInput.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });
}

async function loadHealth() {
  const health = await api("/api/health");
  state.health = health;
  const mode = health.model_mode === "live" ? "Live · " : "API key missing · ";
  setConnection("connected", `${mode}${health.model}`);
  if (state.connectionLost && state.hasConnected) {
    showToast("Python server reconnected. Your transcript and materials are still here.");
  }
  state.connectionLost = false;
  state.hasConnected = true;
}

function openApiKeyDialog() {
  elements.apiKeyInput.value = "";
  elements.apiKeyDialog.showModal();
  elements.apiKeyInput.focus();
}

function closeApiKeyDialog() {
  elements.apiKeyInput.value = "";
  elements.apiKeyDialog.close();
}

async function saveApiKey(event) {
  event.preventDefault();
  const apiKey = elements.apiKeyInput.value;
  elements.saveApiKey.disabled = true;
  try {
    await api("/api/settings/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    closeApiKeyDialog();
    await loadHealth();
    showToast("API key saved to .env. Gemini is ready.");
  } catch (error) {
    showToast(`Could not save API key: ${error.message}`);
  } finally {
    elements.apiKeyInput.value = "";
    elements.saveApiKey.disabled = false;
  }
}

async function reloadAgent() {
  if (state.reloadingAgent || state.thinking) return;
  state.reloadingAgent = true;
  elements.reloadAgent.disabled = true;
  elements.reloadAgent.classList.add("is-reloading");
  elements.reloadAgent.title = "Reloading Python and .env settings";
  elements.reloadAgent.setAttribute("aria-label", "Reloading Python and .env settings");
  setConnection("reloading", "Reloading Agent…");
  logDebug("ui", "reload_agent", "started");

  try {
    for (let attempt = 1; attempt <= RELOAD_ATTEMPTS; attempt += 1) {
      try {
        await loadHealth();
        await loadApplicationData();
        logDebug("ui", "reload_agent", "completed", { attempt });
        showToast("Agent reloaded. Python and .env settings are current.");
        return;
      } catch (error) {
        state.connectionLost = true;
        logDebug("ui", "reload_agent", "retrying", { attempt, error_kind: error.name });
        setConnection("reloading", "Waiting for Python server…");
        if (attempt === RELOAD_ATTEMPTS) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, RELOAD_DELAY_MS));
      }
    }
  } catch (error) {
    logDebug("ui", "reload_agent", "failed", { error_kind: error.name });
    showToast(`Reload failed: ${error.message}`);
  } finally {
    state.reloadingAgent = false;
    elements.reloadAgent.disabled = state.thinking;
    elements.reloadAgent.classList.remove("is-reloading");
    elements.reloadAgent.title = "Reload Python and .env settings";
    elements.reloadAgent.setAttribute("aria-label", "Reload Python and .env settings");
  }
}

async function loadStages() {
  const data = await api("/api/stages");
  state.stages = data.stages;
  if (!state.stages.some((stage) => stage.id === state.stage)) state.stage = state.stages[0].id;
  elements.stageSelect.innerHTML = state.stages
    .map((stage, index) => `<option value="${escapeHtml(stage.id)}">Lab ${index + 1} · ${escapeHtml(stage.title)}${stage.available ? "" : " · coming next"}</option>`)
    .join("");
  elements.stageSelect.value = state.stage;
  renderCapabilityCard();
  renderBackendControl();
  renderMaterialControls();
}

async function loadBackends() {
  const data = await api("/api/backends");
  state.backends = data.backends;
  const selected = state.backends.find((backend) => backend.id === state.backend && backend.available);
  if (!selected) state.backend = "thin_harness";
  elements.backendSelect.innerHTML = state.backends.map((backend) => `
    <option value="${escapeHtml(backend.id)}" ${backend.available ? "" : "disabled"}>
      ${escapeHtml(backend.title)}${backend.available ? "" : " · unavailable"}
    </option>
  `).join("");
  elements.backendSelect.value = state.backend;
  renderBackendControl();
}

function renderBackendControl() {
  elements.backendControl.hidden = state.stage !== "lab_07";
}

async function loadMaterials() {
  const data = await api(`/api/materials?workspace_id=${encodeURIComponent(state.workspaceId)}`);
  state.materials = data.materials;
  renderMaterials();
}

async function loadDefaultMaterials() {
  const data = await api(`/api/materials/defaults?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "POST" });
  state.materials = data.materials;
  localStorage.setItem("harness.materialsInitialized", "true");
  localStorage.setItem("harness.defaultsStage", currentStageNumber() >= 3 ? "3" : "1");
  renderMaterials();
  showToast("Synthetic profile, JD, and sources are ready.");
}

async function ensureStageDefaults() {
  const initialized = localStorage.getItem("harness.materialsInitialized");
  const defaultsStage = Number(localStorage.getItem("harness.defaultsStage") || 0);
  const needsInitialDefaults = !state.materials.length && !initialized;
  const hasCoreMaterials = state.materials.some((material) => material.kind !== "web_source");
  const needsLab3Defaults = currentStageNumber() >= 3 && defaultsStage < 3 && hasCoreMaterials;
  if (needsInitialDefaults || needsLab3Defaults) await loadDefaultMaterials();
}

function renderMaterials() {
  const stageNumber = currentStageNumber();
  const coreMaterials = state.materials.filter((material) => material.kind !== "web_source");
  const webSources = stageNumber >= 3
    ? state.materials.filter((material) => material.kind === "web_source")
    : [];
  const pageCount = Math.max(1, Math.ceil(webSources.length / WEB_SOURCES_PER_PAGE));
  state.materialSourcePage = Math.min(state.materialSourcePage, pageCount - 1);
  const pageStart = state.materialSourcePage * WEB_SOURCES_PER_PAGE;
  const pagedSources = webSources.slice(pageStart, pageStart + WEB_SOURCES_PER_PAGE);
  const visibleMaterials = [...coreMaterials, ...pagedSources];
  if (!visibleMaterials.length) {
    elements.materialsList.innerHTML = `
      <div class="empty-materials">
        <p>No materials yet. Upload or paste your own, or bring back the course examples.</p>
        <button id="restore-example-materials" class="button secondary compact" type="button">Restore example materials</button>
      </div>
    `;
    document.querySelector("#restore-example-materials").addEventListener("click", restoreExampleMaterials);
    elements.materialsPagination.hidden = true;
    return;
  }
  elements.materialsList.innerHTML = visibleMaterials.map((material) => `
    <article class="material-card ${material.kind === "web_source" ? "source-card" : ""}">
      <button class="icon-button delete-material" type="button" data-material-id="${escapeHtml(material.material_id)}" aria-label="Delete ${escapeHtml(material.display_name)}">×</button>
      <div class="material-kind">${materialKindLabel(material, stageNumber)} · ${escapeHtml(material.source)}</div>
      <div class="material-name" title="${escapeHtml(material.display_name)}">${escapeHtml(material.display_name)}</div>
      <div class="material-meta">${material.status === "pending" ? "Waiting for Lab 3 Agent" : `Ready · ${material.characters.toLocaleString()} characters`}</div>
      ${material.source_url ? `<div class="material-url" title="${escapeHtml(material.source_url)}">${escapeHtml(material.source_url)}</div>` : ""}
      <p class="material-preview">${escapeHtml(material.preview)}</p>
    </article>
  `).join("");
  document.querySelectorAll("[data-material-id]").forEach((button) => {
    button.addEventListener("click", () => deleteMaterial(button.dataset.materialId));
  });
  renderMaterialsPagination(webSources.length, pageStart, pagedSources.length, pageCount);
}

function renderMaterialsPagination(sourceCount, pageStart, visibleCount, pageCount) {
  if (sourceCount <= WEB_SOURCES_PER_PAGE) {
    elements.materialsPagination.hidden = true;
    return;
  }
  elements.materialsPagination.hidden = false;
  elements.materialsPagination.innerHTML = `
    <button class="text-button" type="button" data-source-page="previous" ${state.materialSourcePage === 0 ? "disabled" : ""}>Previous</button>
    <span>Sources ${pageStart + 1}–${pageStart + visibleCount} of ${sourceCount}</span>
    <button class="text-button" type="button" data-source-page="next" ${state.materialSourcePage === pageCount - 1 ? "disabled" : ""}>Next</button>
  `;
  elements.materialsPagination.querySelectorAll("[data-source-page]").forEach((button) => {
    button.addEventListener("click", () => {
      state.materialSourcePage += button.dataset.sourcePage === "next" ? 1 : -1;
      renderMaterials();
    });
  });
}

function materialKindLabel(material, stageNumber) {
  if (material.kind === "candidate_profile") return "Candidate profile";
  if (material.kind === "job_description") return "Job description";
  if (material.status !== "ready") return "Queued Web source";
  return stageNumber === 3 ? "Web source" : "Evidence source";
}

function renderMaterialControls() {
  const stageNumber = currentStageNumber();
  elements.webSourceForm.hidden = stageNumber !== 3;
  if (stageNumber < 3) {
    elements.materialsIntro.textContent = "Add a resume and paste or upload the target JD. Web access is introduced in Lab 3.";
  } else if (stageNumber === 3) {
    elements.materialsIntro.textContent = "Add public job or company URLs. The Agent fetches them as Web sources when this Lab runs.";
  } else {
    elements.materialsIntro.textContent = "Web sources fetched in Lab 3 now provide title, URL, and exact snippets for claim-level Evidence checks.";
  }
}

async function uploadSelectedFile() {
  const file = elements.materialFile.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("workspace_id", state.workspaceId);
  form.append("kind", elements.materialKind.value);
  form.append("file", file);
  const fileButton = elements.materialFile.closest(".file-button");
  elements.materialFile.disabled = true;
  fileButton.classList.add("is-loading");
  elements.uploadLabel.textContent = "Reading locally…";
  try {
    await api("/api/materials/upload", { method: "POST", body: form });
    await loadMaterials();
    elements.privacyBanner.hidden = false;
    showToast(`${file.name} added to the local workspace.`);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.materialFile.disabled = false;
    fileButton.classList.remove("is-loading");
    elements.uploadLabel.textContent = "Upload file";
    elements.materialFile.value = "";
  }
}

async function pasteMaterial(event) {
  event.preventDefault();
  try {
    await api("/api/materials/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_id: state.workspaceId,
        kind: elements.materialKind.value,
        display_name: elements.pasteName.value,
        text: elements.pasteText.value,
      }),
    });
    elements.pasteDialog.close();
    elements.pasteText.value = "";
    await loadMaterials();
    elements.privacyBanner.hidden = false;
    showToast("Pasted text added to the local workspace.");
  } catch (error) {
    showToast(error.message);
  }
}

async function addWebSource(event) {
  event.preventDefault();
  const url = elements.webSourceUrl.value.trim();
  if (!url) return;
  try {
    await api("/api/materials/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_id: state.workspaceId, url }),
    });
    elements.webSourceUrl.value = "";
    await loadMaterials();
    showToast("URL queued. Run Lab 3 to fetch it as a Web source.");
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteMaterial(materialId) {
  try {
    await api(`/api/materials/${encodeURIComponent(materialId)}?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "DELETE" });
    await loadMaterials();
    showToast("Material deleted.");
  } catch (error) {
    showToast(error.message);
  }
}

async function clearJobWorkspace() {
  if (!window.confirm("Clear every profile, JD, and source from this local workspace?")) return;
  try {
    await api(`/api/materials?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "DELETE" });
    localStorage.setItem("harness.materialsInitialized", "true");
    localStorage.setItem("harness.defaultsStage", String(Math.max(1, currentStageNumber())));
    await loadMaterials();
    showToast("Job workspace cleared.");
  } catch (error) {
    showToast(error.message);
  }
}

async function restoreExampleMaterials() {
  try {
    await loadDefaultMaterials();
  } catch (error) {
    showToast(error.message);
  }
}

function renderCapabilityCard() {
  const stage = currentStage();
  if (!stage) return;
  const examples = stage.examples.length
    ? `<div class="example-row">${stage.examples.map((example, index) => `<button class="example-button" type="button" data-example="${index}">Try example ${index + 1} · ${escapeHtml(example.id.replaceAll("_", " "))}</button>`).join("")}</div>`
    : "";
  elements.capabilityCard.innerHTML = `
    <div class="capability-top">
      <div>
        <div class="eyebrow">WHEN THIS LAB IS COMPLETE</div>
        <h3 class="capability-title">Now your Agent can…</h3>
        <ul class="capability-list">${stage.now_you_can.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </div>
    </div>
    ${examples}
    <p class="limitation"><strong>Still cannot:</strong> ${escapeHtml(stage.still_cannot)}</p>
  `;
  document.querySelectorAll("[data-example]").forEach((button) => {
    button.addEventListener("click", () => {
      elements.messageInput.value = stage.examples[Number(button.dataset.example)].prompt;
      elements.messageInput.focus();
    });
  });
  const stageNumber = Number(stage.id.split("_")[1]);
  elements.runEval.hidden = stageNumber < 5 || !stage.available;
}

async function runEvalSuite() {
  if (state.thinking) return;
  setThinking(true);
  clearInspector(true);
  try {
    const response = await api("/api/evals/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: state.stage, workspace_id: state.workspaceId }),
    });
    const messages = loadMessages();
    if (response.status === "ok") {
      const summary = response.summary;
      messages.push({
        role: "assistant",
        content: `Eval complete: ${summary.passed}/${summary.total} passed, ${summary.failed} failed. Inspect each task to find the first failure point.`,
      });
    } else {
      messages.push({ role: "error", content: response.error?.message || "Eval failed.", error: response.error });
    }
    saveMessages(messages);
    renderTranscript();
    renderInspector(response);
  } catch (error) {
    showToast(error.message);
  } finally {
    setThinking(false);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = elements.messageInput.value.trim();
  if (!content || state.thinking) return;
  const messages = loadMessages();
  messages.push({ role: "user", content });
  saveMessages(messages);
  elements.messageInput.value = "";
  renderTranscript();
  setThinking(true);
  clearInspector(true);

  try {
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stage: state.stage,
        backend: state.backend,
        session_id: state.sessionId,
        workspace_id: state.workspaceId,
        messages: requestMessages(messages),
      }),
    });
    if (currentStageNumber() >= 3) await loadMaterials();
    if (response.status === "ok") {
      messages.push({ role: "assistant", content: response.assistant_message });
      saveMessages(messages);
    } else {
      messages.push({ role: "error", content: response.error?.message || "The Lab run failed.", error: response.error });
      saveMessages(messages);
      restoreDraft(content);
    }
    renderTranscript();
    renderInspector(response);
  } catch (error) {
    messages.push({ role: "error", content: error.message });
    saveMessages(messages);
    restoreDraft(content);
    renderTranscript();
    setConnection("reloading", "Reconnecting…");
    state.connectionLost = true;
  } finally {
    setThinking(false);
  }
}

function renderTranscript() {
  const messages = loadMessages();
  if (!messages.length) {
    elements.transcript.innerHTML = `<div class="empty-chat"><div class="empty-icon" aria-hidden="true">↗</div><h3>Try your current Agent</h3><p>Choose an example above or ask about the attached profile and job description.</p></div>`;
    return;
  }
  elements.transcript.innerHTML = messages.map((message) => {
    const role = message.role === "error" ? "error" : message.role;
    const location = message.error?.file ? `<span class="error-location">${escapeHtml(message.error.file)}${message.error.line ? `:${message.error.line}` : ""}</span>` : "";
    const content = role === "assistant"
      ? renderMarkdown(message.content)
      : escapeHtml(message.content).replace(/\n/g, "<br />");
    return `<div class="message ${role}${role === "assistant" ? " markdown" : ""}">${content}${location}</div>`;
  }).join("");
  elements.transcript.scrollTop = elements.transcript.scrollHeight;
}

function renderMarkdown(markdown) {
  const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const fence = lines[index].match(/^ {0,3}(`{3,}|~{3,})\s*(.*)$/);
    if (fence) {
      const marker = fence[1][0];
      const markerLength = fence[1].length;
      const codeLines = [];
      index += 1;
      while (index < lines.length && !new RegExp(`^ {0,3}${marker}{${markerLength},}\\s*$`).test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const language = (fence[2].trim().split(/\s+/)[0] || "").replace(/[^A-Za-z0-9_-]/g, "");
      const className = language ? ` class="language-${language}"` : "";
      blocks.push(`<pre><code${className}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = lines[index].match(/^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/);
    if (heading) {
      blocks.push(`<h${heading[1].length}>${renderInlineMarkdown(heading[2])}</h${heading[1].length}>`);
      index += 1;
      continue;
    }

    if (/^ {0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(lines[index])) {
      blocks.push("<hr />");
      index += 1;
      continue;
    }

    if (/^ {0,3}>/.test(lines[index])) {
      const quoteLines = [];
      while (index < lines.length && (/^ {0,3}>/.test(lines[index]) || !lines[index].trim())) {
        quoteLines.push(lines[index].replace(/^ {0,3}> ?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    const list = lines[index].match(/^ {0,3}([-+*]|\d+[.)])\s+(.*)$/);
    if (list) {
      const ordered = /^\d/.test(list[1]);
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(/^ {0,3}([-+*]|\d+[.)])\s+(.*)$/);
        if (!item || /^\d/.test(item[1]) !== ordered) break;
        const itemLines = [item[2]];
        index += 1;
        while (index < lines.length && /^ {2,}\S/.test(lines[index]) && !/^ {0,3}([-+*]|\d+[.)])\s+/.test(lines[index])) {
          itemLines.push(lines[index].trim());
          index += 1;
        }
        items.push(`<li>${renderInlineMarkdown(itemLines.join("\n"))}</li>`);
      }
      const tag = ordered ? "ol" : "ul";
      blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    const paragraphLines = [lines[index]];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n"))}</p>`);
  }

  return blocks.join("");
}

function isMarkdownBlockStart(line) {
  return /^ {0,3}(`{3,}|~{3,})/.test(line)
    || /^ {0,3}#{1,6}\s+/.test(line)
    || /^ {0,3}>/.test(line)
    || /^ {0,3}([-+*]|\d+[.)])\s+/.test(line)
    || /^ {0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(line);
}

function renderInlineMarkdown(value) {
  let html = escapeHtml(value);
  const protectedTokens = [];
  const protect = (markup) => {
    const token = `\u0000${protectedTokens.length}\u0000`;
    protectedTokens.push(markup);
    return token;
  };

  html = html.replace(/`([^`\n]+)`/g, (_, code) => protect(`<code>${code}</code>`));
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^)]*["'])?\)/g, (_, label, href) => {
    if (!safeMarkdownHref(href)) return label;
    return protect(`<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  });
  html = html.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/ {2,}\n/g, "<br />").replace(/\n/g, "<br />");
  return html.replace(/\u0000(\d+)\u0000/g, (_, tokenIndex) => protectedTokens[Number(tokenIndex)] || "");
}

function safeMarkdownHref(value) {
  return /^(https?:|mailto:)/i.test(String(value).trim());
}

function renderInspector(response) {
  elements.runId.textContent = response.run_id;
  elements.runId.title = response.run_id;
  if (!response.events.length) {
    elements.events.innerHTML = `<div class="empty-inspector">No trace events returned.</div>`;
  } else {
    elements.events.innerHTML = response.events.map((event) => `
      <article class="event-card" data-status="${escapeHtml(event.status)}">
        <div class="event-type">${escapeHtml(event.type)} · ${escapeHtml(event.status)}</div>
        <div class="event-operation">${escapeHtml(event.component)}.${escapeHtml(event.operation)}()</div>
        <div class="event-summary">${escapeHtml(event.summary)}</div>
        <div class="event-meta">step ${event.sequence}${event.duration_ms === null ? "" : ` · ${event.duration_ms} ms`}</div>
        ${Object.keys(event.details || {}).length ? `<details class="event-details"><summary>Input / output summary</summary><pre>${escapeHtml(JSON.stringify(event.details, null, 2))}</pre></details>` : ""}
      </article>
    `).join("");
  }
  const stateData = response.state_summary || response.summary || {};
  if (Object.keys(stateData).length) {
    elements.stateSummary.hidden = false;
    elements.stateSummary.innerHTML = `<div class="section-label">Current state / summary</div><pre>${escapeHtml(JSON.stringify(stateData, null, 2))}</pre>`;
  } else {
    elements.stateSummary.hidden = true;
  }
  if (response.artifacts?.length) {
    elements.artifacts.hidden = false;
    elements.artifacts.innerHTML = `<div class="section-label">Artifacts</div>${response.artifacts.map((artifact) => `<a class="artifact-link" target="_blank" href="/api/artifacts/${artifact.path.split("/").map(encodeURIComponent).join("/")}">↗ ${escapeHtml(artifact.label)}</a>`).join("")}`;
  } else {
    elements.artifacts.hidden = true;
  }
}

function clearInspector(loading = false) {
  elements.runId.textContent = loading ? "Running…" : "No run";
  elements.events.innerHTML = `<div class="empty-inspector"><span class="trace-line"></span>${loading ? `Running ${escapeHtml(state.stage.replace("_", " "))}…` : "Send a message to see the call trace."}</div>`;
  elements.stateSummary.hidden = true;
  elements.artifacts.hidden = true;
}

function loadMessages() {
  try { return JSON.parse(localStorage.getItem(messageKey()) || "[]"); }
  catch { return []; }
}

function saveMessages(messages) {
  localStorage.setItem(messageKey(), JSON.stringify(messages));
}

function requestMessages(messages) {
  return messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .slice(-MAX_REQUEST_MESSAGES)
    .map((message) => ({
      role: message.role,
      content: String(message.content).slice(0, MAX_REQUEST_MESSAGE_CHARACTERS),
    }));
}

function restoreDraft(content) {
  if (!elements.messageInput.value) elements.messageInput.value = content;
}

function messageKey() { return `harness.messages.${state.stage}`; }
function currentStage() { return state.stages.find((stage) => stage.id === state.stage); }
function currentStageNumber() { return Number(state.stage.split("_")[1]); }

function setThinking(value) {
  state.thinking = value;
  document.body.dataset.thinking = String(value);
  elements.sendButton.disabled = value;
  elements.runEval.disabled = value;
  elements.reloadAgent.disabled = value || state.reloadingAgent;
  elements.messageInput.disabled = value;
}

function setConnection(status, label) {
  elements.status.dataset.status = status;
  elements.statusLabel.textContent = label;
}

async function pollHealth() {
  const shouldReloadApplicationData = state.connectionLost || !state.stages.length || !state.backends.length;
  try {
    await loadHealth();
    if (shouldReloadApplicationData) await loadApplicationData();
  } catch {
    state.connectionLost = true;
    setConnection("reloading", "Reconnecting…");
  }
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const started = performance.now();
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    logDebug("api", `${method} ${path}`, "network_error", {
      duration_ms: Math.round(performance.now() - started),
      error_kind: error.name,
    });
    throw error;
  }
  let data;
  try { data = await response.json(); }
  catch { data = null; }
  if (!response.ok) {
    logDebug("api", `${method} ${path}`, "failed", {
      status: response.status,
      duration_ms: Math.round(performance.now() - started),
      error: debugErrorSummary(data?.detail),
    });
    throw new Error(readableErrorDetail(data?.detail, response.status));
  }
  if (path !== "/api/health") {
    logDebug("api", `${method} ${path}`, "completed", {
      status: response.status,
      duration_ms: Math.round(performance.now() - started),
    });
  }
  return data;
}

function logDebug(source, operation, status, details = {}) {
  const entry = {
    timestamp: new Date().toISOString(),
    source,
    operation: sanitizeDebugOperation(operation),
    status,
    details,
  };
  state.debugLog.push(entry);
  if (state.debugLog.length > DEBUG_LOG_LIMIT) state.debugLog.shift();
  const method = status === "failed" || status === "network_error" ? "error" : "info";
  console[method]("[Harness Lab]", entry);
}

function sanitizeDebugOperation(operation) {
  return String(operation).replace(/workspace_id=[^&]+/g, "workspace_id=[redacted]");
}

function debugErrorSummary(detail) {
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return undefined;
  return {
    kind: detail.kind || undefined,
    file: detail.file || undefined,
    line: detail.line || undefined,
  };
}

async function copyDebugLog() {
  const report = {
    generated_at: new Date().toISOString(),
    stage: state.stage,
    backend: state.backend,
    model: state.health?.model || "unknown",
    model_mode: state.health?.model_mode || "unknown",
    entries: state.debugLog,
  };
  try {
    await navigator.clipboard.writeText(`Harness Lab Debug Log\n${JSON.stringify(report, null, 2)}`);
    showToast("Debug log copied. Paste it into Codex.");
  } catch {
    showToast("Could not copy the debug log. Allow clipboard access and try again.");
  }
}

function readableErrorDetail(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail) {
    try { return JSON.stringify(detail); }
    catch { /* fall through to the status message */ }
  }
  return `Request failed (${status})`;
}

let toastTimer;
function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2800);
}

function createId(prefix) {
  const value = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${value.replaceAll("-", "")}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

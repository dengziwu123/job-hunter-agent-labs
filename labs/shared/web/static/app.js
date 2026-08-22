const MAX_REQUEST_MESSAGES = 80;
const MAX_REQUEST_MESSAGE_CHARACTERS = 50_000;
const RELOAD_ATTEMPTS = 12;
const RELOAD_DELAY_MS = 500;
const DEBUG_LOG_LIMIT = 100;
const WEB_SOURCES_PER_PAGE = window.matchMedia("(max-height: 800px)").matches ? 1 : 2;
const DEFAULT_PROVIDER_MODELS = {
  gemini: "gemini-3.1-flash-lite",
  openai: "gpt-5-mini",
  anthropic: "claude-haiku-4-5",
};

const state = {
  stages: [],
  backends: [],
  stage: localStorage.getItem("harness.currentStage") || "lab_01",
  backend: localStorage.getItem("harness.productBackend") || "thin_harness",
  workspaceId: localStorage.getItem("harness.workspaceId") || createId("workspace"),
  sessionId: localStorage.getItem("harness.sessionId") || createId("session"),
  materials: [],
  thinking: false,
  clearing: false,
  materialsMutating: false,
  connectionLost: false,
  hasConnected: false,
  loadingApplicationData: false,
  reloadingAgent: false,
  debugLog: [],
  health: null,
  materialSourcePage: 0,
  loadingInstructions: false,
  chatRetry: null,
  comparison: ComparisonState.freshComparisonState(),
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
  openInstructions: document.querySelector("#open-instructions"),
  openDiff: document.querySelector("#open-diff"),
  appShell: document.querySelector("#app-shell"),
  instructionsDialog: document.querySelector("#instructions-dialog"),
  closeInstructions: document.querySelector("#close-instructions"),
  closeInstructionsFooter: document.querySelector("#close-instructions-footer"),
  instructionsTitle: document.querySelector("#instructions-title"),
  instructionsSource: document.querySelector("#instructions-source"),
  instructionsContent: document.querySelector("#instructions-content"),
  diffWorkspace: document.querySelector("#diff-workspace"),
  closeDiff: document.querySelector("#close-diff"),
  diffTitle: document.querySelector("#diff-title"),
  diffPrompt: document.querySelector("#diff-prompt"),
  diffSnapshot: document.querySelector("#diff-snapshot"),
  rerunDiff: document.querySelector("#rerun-diff"),
  resetDiff: document.querySelector("#reset-diff"),
  diffReady: document.querySelector("#diff-ready"),
  diffInputSummary: document.querySelector("#diff-input-summary"),
  diffError: document.querySelector("#diff-error"),
  diffLoading: document.querySelector("#diff-loading"),
  diffContent: document.querySelector("#diff-content"),
  diffResponseStatus: document.querySelector("#diff-response-status"),
  diffDialogGrid: document.querySelector("#diff-dialog-grid"),
  diffSpanInspector: document.querySelector("#diff-span-inspector"),
  apiKeyDialog: document.querySelector("#api-key-dialog"),
  apiKeyForm: document.querySelector("#api-key-form"),
  providerSelect: document.querySelector("#provider-select"),
  modelInput: document.querySelector("#model-input"),
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
  addWebSource: document.querySelector("#add-web-source"),
  capabilityCard: document.querySelector("#capability-card"),
  exampleActions: document.querySelector("#example-actions"),
  transcript: document.querySelector("#chat-transcript"),
  clearChat: document.querySelector("#clear-chat"),
  runEval: document.querySelector("#run-eval"),
  chatForm: document.querySelector("#chat-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  sendLabel: document.querySelector("#send-button .send-label"),
  composerHint: document.querySelector("#composer-hint"),
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
    closeInstructionsDialog();
    closeDiffWorkspace();
    resetComparisonState();
    state.stage = elements.stageSelect.value;
    localStorage.setItem("harness.currentStage", state.stage);
    renderCapabilityCard();
    renderTranscript();
    clearInspector();
    renderBackendControl();
    renderMaterialControls();
    await loadMaterials();
    await ensureStageDefaults();
    renderDiffButtonState();
  });
  elements.backendSelect.addEventListener("change", () => {
    state.backend = elements.backendSelect.value;
    localStorage.setItem("harness.productBackend", state.backend);
    invalidateComparison();
  });
  elements.reloadAgent.addEventListener("click", reloadAgent);
  elements.openInstructions.addEventListener("click", openInstructions);
  elements.openDiff.addEventListener("click", openDiffWorkspace);
  elements.closeDiff.addEventListener("click", closeDiffWorkspace);
  elements.rerunDiff.addEventListener("click", () => runComparison(true));
  elements.resetDiff.addEventListener("click", resetDiffWorkspace);
  elements.diffDialogGrid.addEventListener("toggle", (event) => {
    if (event.target.closest?.(".run-evidence-step")) scheduleEvidenceAlignment();
  }, true);
  window.addEventListener("resize", scheduleEvidenceAlignment);
  elements.messageInput.addEventListener("input", renderDiffButtonState);
  elements.closeInstructions.addEventListener("click", closeInstructionsDialog);
  elements.closeInstructionsFooter.addEventListener("click", closeInstructionsDialog);
  elements.instructionsDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeInstructionsDialog();
  });
  elements.status.addEventListener("click", openApiKeyDialog);
  elements.closeApiKey.addEventListener("click", closeApiKeyDialog);
  elements.cancelApiKey.addEventListener("click", closeApiKeyDialog);
  elements.apiKeyDialog.addEventListener("close", () => { elements.apiKeyInput.value = ""; });
  elements.providerSelect.addEventListener("change", () => {
    elements.modelInput.value = DEFAULT_PROVIDER_MODELS[elements.providerSelect.value];
  });
  elements.apiKeyForm.addEventListener("submit", saveApiKey);
  elements.copyDebugLog.addEventListener("click", copyDebugLog);
  elements.materialFile.addEventListener("change", uploadSelectedFile);
  elements.openPaste.addEventListener("click", () => {
    if (!pageExecutionIsActive()) elements.pasteDialog.showModal();
  });
  elements.cancelPaste.addEventListener("click", () => elements.pasteDialog.close());
  elements.pasteForm.addEventListener("submit", pasteMaterial);
  elements.webSourceForm.addEventListener("submit", addWebSource);
  elements.clearMaterials.addEventListener("click", clearJobWorkspace);
  elements.clearChat.addEventListener("click", clearConversation);
  elements.runEval.addEventListener("click", runEvalSuite);
  elements.chatForm.addEventListener("submit", sendMessage);
  elements.messageInput.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });
}

async function openInstructions() {
  if (state.loadingInstructions || !state.stage || elements.instructionsDialog.open) return;
  state.loadingInstructions = true;
  elements.openInstructions.disabled = true;
  elements.instructionsDialog.showModal();
  elements.instructionsTitle.textContent = `Lab ${currentStageNumber()} instructions`;
  elements.instructionsSource.textContent = "Loading source Markdown…";
  elements.instructionsContent.innerHTML = '<p class="instructions-placeholder">Loading the current Lab handout…</p>';
  try {
    const data = await api(`/api/stages/${encodeURIComponent(state.stage)}/instructions`);
    elements.instructionsTitle.textContent = `Lab ${currentStageNumber()} instructions`;
    elements.instructionsSource.textContent = data.source;
    elements.instructionsContent.innerHTML = renderMarkdown(data.markdown);
    elements.instructionsContent.scrollTop = 0;
  } catch {
    renderInstructionsCompatibilityFallback();
  } finally {
    state.loadingInstructions = false;
    elements.openInstructions.disabled = false;
  }
}

function renderInstructionsCompatibilityFallback() {
  elements.instructionsSource.textContent = "Legacy runtime detected";
  elements.instructionsContent.innerHTML = `
    <div class="instructions-compatibility">
      <p class="instructions-compatibility-kicker">Runtime update needed</p>
      <h3>This workspace is using an older Lab server</h3>
      <p>The browser patch is installed, but this Python runtime does not expose the instructions API yet.</p>
      <p>Install the latest shared runtime from the Lab 1 package, then restart the web service. Your browser binary, local configuration, and workspace data will stay in place.</p>
    </div>
  `;
}

function closeInstructionsDialog() {
  if (!elements.instructionsDialog.open || elements.instructionsDialog.dataset.closing === "true") return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    elements.instructionsDialog.close();
    return;
  }
  elements.instructionsDialog.dataset.closing = "true";
  elements.instructionsDialog.classList.add("is-closing");
  elements.instructionsDialog.addEventListener("animationend", () => {
    elements.instructionsDialog.classList.remove("is-closing");
    delete elements.instructionsDialog.dataset.closing;
    elements.instructionsDialog.close();
  }, { once: true });
}

function comparisonPrompt() {
  const draft = elements.messageInput.value.trim();
  if (draft) return draft;
  if (!state.comparison.useTranscriptPrompt) return "";
  return [...loadMessages()].reverse().find((message) => message.role === "user")?.content?.trim() || "";
}

function renderDiffButtonState() {
  const stage = currentStage();
  const available = Boolean(stage?.available && stage.previous_stage);
  elements.openDiff.hidden = !available;
  elements.openDiff.disabled = pageExecutionIsActive();
  elements.openDiff.title = "Compare a prompt across the current Lab and the previous Lab";
}

function resetComparisonState({ open = false, useTranscriptPrompt = true } = {}) {
  state.comparison = ComparisonState.freshComparisonState({ open, useTranscriptPrompt });
}

function invalidateComparison() {
  const comparisonWasOpen = state.comparison.open;
  const storedPrompt = state.comparison.prompt;
  resetComparisonState({ open: comparisonWasOpen });
  if (comparisonWasOpen) {
    if (!elements.messageInput.value.trim() && storedPrompt) {
      elements.messageInput.value = storedPrompt;
    }
    renderDiffHeader(currentStage());
    renderDiff();
    renderComposerMode();
  }
  renderDiffButtonState();
}

function renderDiffHeader(stage) {
  elements.diffTitle.textContent = `Diff · ${displayStageLabel(stage.previous_stage)} → ${displayStageLabel(state.stage)}`;
  const prompt = state.comparison.status === "ready" ? comparisonPrompt() : state.comparison.prompt;
  elements.diffPrompt.textContent = prompt || "Enter a prompt in the composer to compare both Labs.";
  const snapshot = state.comparison.result?.input_snapshot;
  const fingerprint = snapshot?.fingerprint;
  const snapshotLabel = state.comparison.result
    ? `${state.comparison.result.comparison_id} · ${fingerprint || "input fingerprint unavailable"}`
    : "Same input snapshot";
  elements.diffSnapshot.textContent = snapshotLabel;
  elements.diffSnapshot.title = fingerprint ? `Input snapshot ${fingerprint}` : snapshotLabel;
  elements.diffInputSummary.textContent = `${state.materials.length} material${state.materials.length === 1 ? "" : "s"} · one current user request · two isolated complete-stage runs · may use multiple model calls · no transcript changes`;
  if (supportsComparisonHistory()) {
    elements.diffInputSummary.textContent = `${state.materials.length} material${state.materials.length === 1 ? "" : "s"} · shared user-request history · independent Before/After state · no main transcript changes`;
  }
}

function openDiffWorkspace() {
  const stage = currentStage();
  if (!stage?.previous_stage || !stage.available || pageExecutionIsActive()) return;
  if (!comparisonPrompt() && !state.comparison.prompt && stage.examples[0]?.prompt) {
    elements.messageInput.value = stage.examples[0].prompt;
  }
  state.comparison.open = true;
  renderDiffHeader(stage);
  elements.appShell.classList.add("comparison-mode");
  elements.diffWorkspace.hidden = false;
  elements.openDiff.setAttribute("aria-pressed", "true");
  renderComposerMode();
  renderDiff();
}

function closeDiffWorkspace() {
  elements.diffWorkspace.hidden = true;
  elements.exampleActions.hidden = false;
  elements.appShell.classList.remove("comparison-mode");
  elements.openDiff.setAttribute("aria-pressed", "false");
  state.comparison.open = false;
  document.body.dataset.comparisonRunning = "false";
  renderComposerMode();
}

async function resetDiffWorkspace() {
  if (pageExecutionIsActive()) return;
  try {
    if (supportsComparisonHistory()) {
      await api(
        `/api/comparison-state?workspace_id=${encodeURIComponent(state.workspaceId)}&session_id=${encodeURIComponent(state.sessionId)}&current_stage=${encodeURIComponent(state.stage)}`,
        { method: "DELETE" },
      );
    }
    resetComparisonState({ open: true, useTranscriptPrompt: false });
    elements.messageInput.value = "";
    renderDiffHeader(currentStage());
    renderDiff();
    renderComposerMode();
    renderDiffButtonState();
    showToast("Diff reset.");
  } catch (error) {
    showToast(`Diff could not be reset: ${error.message}`);
  }
}

function renderComposerMode() {
  const isComparisonMode = state.comparison.open;
  elements.sendLabel.textContent = isComparisonMode ? "Compare" : "Send";
  elements.composerHint.textContent = isComparisonMode ? "⌘ + Enter to compare" : "⌘ + Enter to send";
  elements.messageInput.placeholder = isComparisonMode ? "Enter a prompt to compare both Labs…" : "Ask your Job Agent…";
  elements.sendButton.setAttribute("aria-label", isComparisonMode ? "Run comparison" : "Send message");
  elements.sendButton.disabled = state.thinking
    || state.clearing
    || state.materialsMutating
    || (isComparisonMode && state.comparison.status === "running");
}

async function runComparison(useStoredPrompt = false) {
  const prompt = useStoredPrompt ? state.comparison.prompt : comparisonPrompt() || state.comparison.prompt;
  if (state.clearing || state.materialsMutating) return;
  if (!useStoredPrompt && state.comparison.retryRequestId) {
    showToast("Rerun the interrupted comparison, or Reset Diff before starting a new prompt.");
    return;
  }
  if (!prompt || state.comparison.status === "running") {
    showToast("Enter a prompt in the composer before running Diff.");
    return;
  }
  const comparisonState = state.comparison;
  const comparisonStage = state.stage;
  const comparisonBackend = state.backend;
  const comparisonProvider = state.health?.provider;
  const comparisonModel = state.health?.model;
  const failedTurn = state.comparison.failedTurn;
  const retryRequestId = useStoredPrompt ? state.comparison.retryRequestId : null;
  const replayingRequest = Boolean(retryRequestId);
  const requestId = retryRequestId || createId("comparison_request");
  const historyPlan = ComparisonState.comparisonHistoryPlan({
    history: state.comparison.history,
    useStoredPrompt,
    result: state.comparison.result,
    failedRequestWasRerun: state.comparison.failedRequestWasRerun,
    failedTurn,
  });
  const replaceLatest = historyPlan.replaceLatest;
  const historyBase = supportsComparisonHistory()
    ? historyPlan.historyBase
    : [];
  const messages = useStoredPrompt && state.comparison.messages.length
    ? state.comparison.messages
    : supportsComparisonHistory()
      ? comparisonHistoryMessages(historyBase, prompt)
      : comparisonMessages(prompt);
  const isCurrentComparison = () => (
    state.comparison === comparisonState
    && state.stage === comparisonStage
    && state.backend === comparisonBackend
    && state.health?.provider === comparisonProvider
    && state.health?.model === comparisonModel
  );
  state.comparison.prompt = prompt;
  state.comparison.messages = messages;
  state.comparison.status = "running";
  state.comparison.error = null;
  state.comparison.selectedSpanId = null;
  state.comparison.replacingLatest = replaceLatest;
  state.comparison.failedTurn = null;
  elements.diffPrompt.textContent = prompt;
  if (!useStoredPrompt) elements.messageInput.value = "";
  renderDiffButtonState();
  renderDiff();
  try {
    if (supportsComparisonHistory() && !state.comparison.serverStateReady && !replayingRequest) {
      await api(
        `/api/comparison-state?workspace_id=${encodeURIComponent(state.workspaceId)}&session_id=${encodeURIComponent(state.sessionId)}&current_stage=${encodeURIComponent(state.stage)}`,
        { method: "DELETE" },
      );
      if (!isCurrentComparison()) return;
      state.comparison.serverStateReady = true;
    } else if (supportsComparisonHistory() && replaceLatest && !replayingRequest) {
      const comparisonId = failedTurn?.result?.comparison_id
        || state.comparison.history.at(-1)?.result?.comparison_id;
      if (!comparisonId) throw new Error("The previous comparison id is unavailable.");
      await api(
        `/api/comparison-state/rollback?workspace_id=${encodeURIComponent(state.workspaceId)}&session_id=${encodeURIComponent(state.sessionId)}&current_stage=${encodeURIComponent(state.stage)}&comparison_id=${encodeURIComponent(comparisonId)}`,
        { method: "POST" },
      );
      if (!isCurrentComparison()) return;
    }
    state.comparison.retryRequestId = requestId;
    const response = await api("/api/comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_stage: state.stage,
        backend: comparisonBackend,
        session_id: state.sessionId,
        workspace_id: state.workspaceId,
        request_id: requestId,
        messages,
      }),
    });
    if (!isCurrentComparison()) return;
    state.comparison.result = response;
    state.comparison.failedRequestWasRerun = false;
    const resultPlan = ComparisonState.comparisonResultPlan({
      historyBase,
      prompt,
      result: response,
      supportsHistory: supportsComparisonHistory(),
      replacedLatest: replaceLatest,
    });
    state.comparison.failedTurn = resultPlan.failedTurn;
    state.comparison.history = resultPlan.history;
    state.comparison.status = resultPlan.status;
    state.comparison.retryRequestId = null;
    acknowledgeComparisonRequest(requestId, comparisonStage);
    renderDiffHeader(currentStage());
  } catch (error) {
    if (!isCurrentComparison()) return;
    state.comparison.error = error;
    state.comparison.status = "error";
    state.comparison.result = null;
    state.comparison.failedRequestWasRerun = replaceLatest;
  } finally {
    if (!isCurrentComparison()) return;
    renderDiff();
    renderDiffButtonState();
    state.comparison.replacingLatest = false;
  }
}

function renderDiff() {
  const status = state.comparison.status;
  document.body.dataset.comparisonRunning = String(state.comparison.open && status === "running");
  elements.diffWorkspace.hidden = !state.comparison.open;
  elements.diffReady.hidden = status !== "ready";
  elements.diffLoading.hidden = status !== "running";
  elements.diffError.hidden = status !== "error" && status !== "full_failure";
  elements.diffContent.hidden = !state.comparison.open;
  elements.exampleActions.hidden = false;
  elements.rerunDiff.hidden = !state.comparison.result && !state.comparison.history.length && status !== "error";
  elements.rerunDiff.disabled = state.clearing || state.materialsMutating || status === "running";
  elements.resetDiff.disabled = state.clearing || state.materialsMutating || status === "running";
  elements.sendButton.disabled = state.thinking
    || state.clearing
    || state.materialsMutating
    || (state.comparison.open && status === "running");
  elements.clearMaterials.disabled = state.thinking || state.clearing || state.materialsMutating || status === "running";
  elements.clearChat.disabled = state.thinking || state.clearing || state.materialsMutating || status === "running";
  syncMaterialControls();
  elements.sendButton.setAttribute("aria-busy", String(status === "running"));
  if (status === "error") {
    elements.diffError.textContent = state.comparison.error?.message || "The comparison could not be completed.";
  } else if (status === "full_failure" && state.comparison.result) {
    elements.diffError.textContent = "Both Lab snapshots failed. Inspect the response panes for the side-specific errors.";
  }
  const history = state.comparison.history;
  if (!state.comparison.result && !history.length) {
    const stage = currentStage();
    elements.diffResponseStatus.textContent = status === "error" ? "Comparison failed · rerun to try again" : "Waiting for comparison";
    elements.diffResponseStatus.dataset.status = status === "error" ? "error" : "pending";
    elements.diffDialogGrid.innerHTML = `<div class="diff-turn-grid">${[
      renderDiffPlaceholder("before", stage?.previous_stage, status),
      renderDiffPlaceholder("after", state.stage, status),
    ].join("")}</div>`;
    elements.diffSpanInspector.hidden = true;
    return;
  }
  const result = state.comparison.result || history.at(-1)?.result;
  const beforeOk = result.before.status === "ok";
  const afterOk = result.after.status === "ok";
  const partial = !beforeOk || !afterOk;
  elements.diffResponseStatus.textContent = !beforeOk && !afterOk
    ? "Both snapshots failed · inspect the errors"
    : partial
      ? "Partial run · inspect the failed side"
      : "Same prompt · separate snapshots";
  elements.diffResponseStatus.dataset.status = partial ? "error" : "ok";
  const renderedTurns = supportsComparisonHistory()
    ? history.map((turn, index) => renderComparisonTurn(turn, index)).join("")
      + (state.comparison.failedTurn
        ? renderComparisonTurn(state.comparison.failedTurn, history.length)
        : "")
    : renderComparisonPair(history.at(-1) || { prompt: state.comparison.prompt, result });
  const pendingTurn = status === "running" && !state.comparison.replacingLatest
    ? renderPendingComparisonTurn(state.comparison.prompt, history.length)
    : "";
  elements.diffDialogGrid.innerHTML = renderedTurns + pendingTurn;
  alignComparisonEvidenceRows();
  bindTraceSpanButtons();
  renderSpanInspector();
}

function renderDiffPlaceholder(side, stageId, status) {
  const stage = state.stages.find((item) => item.id === stageId);
  const title = stage ? `${displayStageLabel(stageId)} · ${stage.title}` : displayStageLabel(stageId);
  const running = status === "running";
  const statusLabel = running ? "Running" : status === "error" ? "Unavailable" : "Ready";
  const copy = running ? "This Lab snapshot is running…" : "Run the comparison to load this Lab's response and trajectory.";
  return `
    <article class="diff-dialog-panel diff-dialog-placeholder-panel" data-side="${side}">
      <header class="diff-dialog-panel-header">
        <div>
          <div class="eyebrow">${side === "before" ? "BEFORE" : "AFTER"}</div>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <span class="diff-dialog-panel-status">${statusLabel}</span>
      </header>
      <section class="diff-dialog-section">
        <div class="diff-dialog-section-label">RESPONSE</div>
        <div class="diff-dialog-placeholder">${copy}</div>
      </section>
      <section class="diff-dialog-section">
        <div class="diff-dialog-section-label">TRAJECTORY</div>
        <div class="diff-dialog-placeholder">Trace spans will appear here after the run.</div>
      </section>
    </article>
  `;
}

function comparisonMessages(prompt) {
  return requestMessages([{ role: "user", content: prompt }]);
}

function supportsComparisonHistory() {
  return currentStageNumber() >= 3;
}

function comparisonHistoryMessages(history, prompt) {
  const messages = history.map((turn) => ({ role: "user", content: turn.prompt }));
  messages.push({ role: "user", content: prompt });
  return requestMessages(messages);
}

function renderComparisonTurn(turn, index) {
  return `
    <section class="diff-turn">
      <header class="diff-turn-header">
        <span>Turn ${index + 1}</span>
        <p>${escapeHtml(turn.prompt)}</p>
      </header>
      ${renderComparisonPair(turn)}
    </section>
  `;
}

function renderPendingComparisonTurn(prompt, index) {
  const stage = currentStage();
  return `
    <section class="diff-turn diff-turn-pending">
      ${supportsComparisonHistory() ? `<header class="diff-turn-header"><span>Turn ${index + 1}</span><p>${escapeHtml(prompt)}</p></header>` : ""}
      <div class="diff-turn-grid">
        ${renderDiffPlaceholder("before", stage?.previous_stage, "running")}
        ${renderDiffPlaceholder("after", state.stage, "running")}
      </div>
    </section>
  `;
}

function renderComparisonPair(turn) {
  const result = turn.result;
  const traceScale = ComparisonState.comparisonTraceScale({
    before: result.before,
    after: result.after,
  });
  const evidenceRows = ComparisonState.comparisonEvidenceRows({
    before: result.before,
    after: result.after,
    delta: result.delta,
  });
  return `
    <div class="diff-turn-grid" data-align-trajectories="true">
      ${renderDiffDialogPanel("before", result.before, result.delta, traceScale, result.comparison_id, evidenceRows)}
      ${renderDiffDialogPanel("after", result.after, result.delta, traceScale, result.comparison_id, evidenceRows)}
    </div>
  `;
}

function renderDiffDialogPanel(side, run, delta, traceScale, comparisonId, evidenceRows) {
  const stage = state.stages.find((item) => item.id === run.stage);
  const title = stage ? `${displayStageLabel(run.stage)} · ${stage.title}` : displayStageLabel(run.stage);
  const body = run.status === "ok"
    ? (run.assistant_message ? renderMarkdown(run.assistant_message) : "<p>No assistant response was returned.</p>")
    : `<p class="diff-response-error">${escapeHtml(run.error?.message || "This snapshot failed before returning a response.")}</p>`;
  const artifacts = run.artifacts?.length
    ? `
      <div class="diff-artifacts">
        <div class="diff-dialog-section-label">ARTIFACTS</div>
        <div class="diff-artifact-links">
          ${run.artifacts.map((artifact) => `<a class="artifact-link diff-artifact-link" target="_blank" rel="noopener noreferrer" href="${artifactHref(artifact.path)}">↗ ${escapeHtml(artifact.label)}</a>`).join("")}
        </div>
      </div>
    `
    : "";
  const label = side === "before" ? "Before" : "After";
  return `
    <article class="diff-dialog-panel" data-side="${side}" data-status="${escapeHtml(run.status)}">
      <header class="diff-dialog-panel-header">
        <div>
          <div class="eyebrow">${label.toUpperCase()}</div>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <span class="diff-dialog-panel-status">${escapeHtml(run.status === "ok" ? "Result" : "Failed")}</span>
      </header>
      <section class="diff-dialog-section">
        <div class="diff-dialog-section-label">RESPONSE</div>
        <div class="diff-response-pane" data-side="${side}" data-status="${escapeHtml(run.status)}">
          <div class="diff-response-body">${body}</div>
        </div>
        ${artifacts}
      </section>
      ${renderRunEvidence(side, run, delta, evidenceRows)}
      <section class="diff-dialog-section">
        <div class="diff-dialog-section-heading">
          <div class="diff-dialog-section-label">TRAJECTORY</div>
          <span class="fine-print">${traceScale.mode === "time" ? "Shared time scale" : "Shared sequence order"}</span>
        </div>
        ${renderTracePanel(side, run, delta, false, traceScale, comparisonId)}
      </section>
    </article>
  `;
}

function renderRunEvidence(side, run, delta, evidenceRows = []) {
  const rows = evidenceRows.length
    ? evidenceRows
    : ComparisonState.comparisonEvidenceRows({
        [side]: { events: run.events, trace: run.trace },
        delta,
      });
  const steps = rows.map((row) => row[side]).filter(Boolean);
  if (!rows.length) {
    return `
      <section class="diff-dialog-section diff-run-evidence" data-side="${side}" data-empty="true">
        <div class="diff-dialog-section-label">${side === "after" ? "WHY OUTPUT CHANGED" : "BASELINE PIPELINE"}</div>
        <p class="run-evidence-summary">No pipeline events were recorded for this run.</p>
      </section>
    `;
  }
  const addedCount = steps.filter((step) => step.change === "added").length;
  const changedCount = steps.filter((step) => step.change === "changed").length;
  const removedCount = steps.filter((step) => step.change === "removed").length;
  const summary = side === "after"
    ? addedCount
      ? `${addedCount} new pipeline ${addedCount === 1 ? "step explains" : "steps explain"} what this Lab added. Open a step to inspect its exact input and output.`
      : changedCount
        ? `${changedCount} inherited ${changedCount === 1 ? "step changed" : "steps changed"} in this Lab. Open the changed step to compare its evidence.`
        : "The pipeline shape is unchanged. Open model steps to compare their exact inputs and outputs."
    : removedCount
      ? `${removedCount} baseline ${removedCount === 1 ? "step is" : "steps are"} removed from the After run.`
      : "Baseline pipeline. Steps without a change badge are inherited by the After run.";
  return `
    <section class="diff-dialog-section diff-run-evidence" data-side="${side}">
      <div class="diff-dialog-section-label">${side === "after" ? "WHY OUTPUT CHANGED" : "BASELINE PIPELINE"}</div>
      <p class="run-evidence-summary">${escapeHtml(summary)}</p>
      <div class="run-evidence-steps">
        ${rows.map((row, index) => renderRunEvidenceRow(row, index, side)).join("")}
      </div>
    </section>
  `;
}

function renderRunEvidenceRow(row, index, side) {
  const step = row[side];
  if (step) {
    return `<div class="run-evidence-row" data-evidence-row="${index}">${renderRunEvidenceStep(step, side)}</div>`;
  }
  const counterpart = row[side === "before" ? "after" : "before"];
  const absentFrom = side === "before" ? "Baseline" : "After";
  return `
    <div class="run-evidence-row" data-evidence-row="${index}">
      <div class="run-evidence-placeholder">
        <span>NOT IN ${absentFrom.toUpperCase()}</span>
        <strong>${escapeHtml(counterpart ? humanizeOperation(counterpart.operation) : "No corresponding step")}</strong>
      </div>
    </div>
  `;
}

function renderRunEvidenceStep(step, side) {
  const changeLabel = {
    added: "NEW IN AFTER",
    removed: "REMOVED FROM AFTER",
    changed: "CHANGED",
  }[step.change];
  const duration = Number.isFinite(step.duration_ms) ? ` · ${formatDuration(step.duration_ms)}` : "";
  return `
    <article class="run-evidence-step" data-kind="${escapeHtml(step.type)}" data-change="${escapeHtml(step.change)}">
      <header class="run-evidence-step-header">
        <div class="run-evidence-step-number">${step.sequence}</div>
        <div class="run-evidence-step-copy">
          <div class="run-evidence-step-title">
            <span>${escapeHtml(evidenceKindLabel(step.type))}</span>
            <strong>${escapeHtml(humanizeOperation(step.operation))}</strong>
          </div>
          <p>${escapeHtml(step.summary || "Recorded pipeline event.")}</p>
        </div>
        ${changeLabel ? `<span class="run-evidence-change" data-change="${step.change}">${changeLabel}</span>` : ""}
      </header>
      <div class="run-evidence-meta">${escapeHtml(step.component)}${duration}</div>
      ${renderEvidenceChangeSummary(step)}
      ${renderRunEvidencePreview(step.details)}
      ${renderEventDetails(step.details, false, {
        changedFields: step.changed_fields,
        fieldComparisons: step.io_field_comparisons,
        side,
      })}
    </article>
  `;
}

function renderEvidenceChangeSummary(step) {
  if (step.change !== "changed") return "";
  const labels = (step.changed_fields || []).map(evidenceFieldLabel);
  const message = labels.length
    ? `Input changed: ${labels.join(", ")}`
    : "Execution contract changed";
  return `<div class="run-evidence-change-summary">${escapeHtml(message)}</div>`;
}

function evidenceFieldLabel(key) {
  const labels = {
    system_prompt: "System prompt",
    user_request: "User request",
    request: "Request",
    messages: "Messages",
    input: "Tool input",
    tool_args: "Tool arguments",
    provider_input: "Provider input",
    actual_provider_input: "Provider input",
    reconstructed_provider_input: "Provider input",
    provider_input_mode: "Provider input format",
  };
  return labels[key] || humanizeOperation(key);
}

let evidenceAlignmentFrame;
function scheduleEvidenceAlignment() {
  window.cancelAnimationFrame(evidenceAlignmentFrame);
  evidenceAlignmentFrame = window.requestAnimationFrame(alignComparisonEvidenceRows);
}

function alignComparisonEvidenceRows() {
  const stacked = window.matchMedia("(max-width: 820px)").matches;
  elements.diffDialogGrid.querySelectorAll(".diff-turn-grid[data-align-trajectories='true']").forEach((grid) => {
    const rows = grid.querySelectorAll(".run-evidence-row");
    rows.forEach((row) => { row.style.minHeight = ""; });
    if (stacked) return;
    const indexes = new Set([...rows].map((row) => row.dataset.evidenceRow));
    indexes.forEach((index) => {
      const aligned = grid.querySelectorAll(`.run-evidence-row[data-evidence-row="${index}"]`);
      const height = Math.max(...[...aligned].map((row) => row.getBoundingClientRect().height));
      aligned.forEach((row) => { row.style.minHeight = `${Math.ceil(height)}px`; });
    });
  });
}

function evidenceKindLabel(value) {
  const labels = {
    model_call: "Model",
    tool_call: "Tool call",
    tool_result: "Tool result",
    tool_output: "Tool result",
    guardrail: "Policy",
    policy: "Policy",
    approval_decision: "Approval",
    validation: "Validation",
    state_load: "State",
    state_update: "State",
    delegation: "Delegation",
    handoff: "Handoff",
    skill: "Skill",
    evidence: "Evidence",
    eval: "Eval",
    trace: "Trace",
    artifact: "Artifact",
    capability_boundary: "Boundary",
  };
  return labels[value] || humanizeOperation(value);
}

function humanizeOperation(value) {
  return String(value || "run")
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderRunEvidencePreview(details = {}) {
  const items = [];
  if (details.from || details.to) items.push(["Flow", [details.from, details.to].filter(Boolean).join(" → ")]);
  if (details.tool) items.push(["Tool", details.tool]);
  if (details.input !== undefined) items.push(["Input", details.input]);
  if (Array.isArray(details.source_ids)) items.push(["Sources", details.source_ids]);
  if (details.revision !== undefined) items.push(["Revision", details.revision]);
  if (details.action_type) items.push(["Action", details.action_type]);
  if (details.status) items.push(["Decision", details.status]);
  if (details.reason) items.push(["Reason", details.reason]);
  if (details.model_calls !== undefined) items.push(["Model calls", details.model_calls]);
  if (!items.length) return "";
  return `
    <dl class="run-evidence-preview">
      ${items.slice(0, 4).map(([label, value]) => `
        <div>
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(formatEvidenceValue(value))}</dd>
        </div>
      `).join("")}
    </dl>
  `;
}

function formatEvidenceValue(value) {
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  const text = serialized === undefined ? String(value) : serialized;
  return text.length > 180 ? `${text.slice(0, 177)}…` : text;
}

function renderTracePanel(side, run, delta, includeHeading = true, traceScale = null, comparisonId = run.run_id) {
  const trace = run.trace || { participants: [], spans: [], links: [] };
  const participants = trace.participants?.length ? trace.participants : [{ participant_id: "workflow", label: "Workflow" }];
  const spans = trace.spans || [];
  const scale = traceScale || ComparisonState.comparisonTraceScale({ [side]: run });
  const total = scale.total;
  const sequenceContentWidth = scale.mode === "sequence"
    ? Math.max(540, total * 92 + 120)
    : 540;
  const spanIndexes = new Map(spans.map((span, index) => [span.span_id, index]));
  const geometryById = new Map(
    spans.map((span, index) => [
      span.span_id,
      ComparisonState.traceSpanGeometry({ span, index, scale }),
    ]),
  );
  const addedKeys = new Set((delta?.added_spans || []).map((span) => span.semantic_key));
  const addedOccurrences = new Set((delta?.added_span_keys || []).map((item) => `${item.semantic_key}:${item.occurrence}`));
  const removedKeys = new Set((delta?.removed_spans || []).map((span) => span.semantic_key));
  const removedOccurrences = new Set((delta?.removed_span_keys || []).map((item) => `${item.semantic_key}:${item.occurrence}`));
  const spanOccurrences = new Map();
  const occurrenceCounts = {};
  spans.forEach((span) => {
    const occurrence = occurrenceCounts[span.semantic_key] || 0;
    spanOccurrences.set(span.span_id, `${span.semantic_key}:${occurrence}`);
    occurrenceCounts[span.semantic_key] = occurrence + 1;
  });
  const axis = [0, .25, .5, .75, 1].map((fraction) => {
    const label = scale.mode === "time"
      ? formatDuration(Math.round(total * fraction))
      : `event ${Math.min(total, Math.max(1, Math.floor(total * fraction) + 1))}`;
    return `<span style="left:${fraction * 100}%">${label}</span>`;
  }).join("");
  const laneData = participants.map((participant) => {
    const laneSpans = spans.filter((span) => span.participant_id === participant.participant_id);
    const layout = layoutTraceSpans(laneSpans, geometryById);
    const trackHeight = Math.max(1, layout.rows) * 30 + 8;
    const spanMarkup = laneSpans.map((span) => {
      const position = layout.positions[span.span_id] || { row: 0 };
      const geometry = geometryById.get(span.span_id)
        || ComparisonState.traceSpanGeometry({
          span,
          index: spanIndexes.get(span.span_id) || 0,
          scale,
        });
      const exceptional = span.status === "failed" || span.status === "blocked";
      const statusLabel = exceptional ? ` · ${span.status}` : "";
      const label = `${span.operation}${statusLabel}`;
      const isNew = addedOccurrences.size ? addedOccurrences.has(spanOccurrences.get(span.span_id)) : addedKeys.has(span.semantic_key);
      const isRemoved = removedOccurrences.size ? removedOccurrences.has(spanOccurrences.get(span.span_id)) : removedKeys.has(span.semantic_key);
      const changeLabel = side === "after" && isNew ? " · new in After" : side === "before" && isRemoved ? " · removed in After" : "";
      return `<button class="trace-span" type="button" data-comparison-id="${escapeHtml(comparisonId)}" data-side="${side}" data-span-id="${escapeHtml(span.span_id)}" data-kind="${escapeHtml(span.kind)}" data-status="${escapeHtml(span.status)}" data-point="false" data-new="${side === "after" && isNew}" data-removed="${side === "before" && isRemoved}" aria-label="${escapeHtml(label + changeLabel)}" style="left:${geometry.startPercent}%;width:${geometry.widthPercent}%;top:${position.row * 30 + 4}px" title="${escapeHtml(span.component)}.${escapeHtml(span.operation)} · ${escapeHtml(formatDuration(span.duration_ms))}${changeLabel}"><span class="trace-span-label">${escapeHtml(label)}</span></button>`;
    }).join("");
    return { participant, laneSpans, layout, trackHeight, spanMarkup };
  });
  const spanById = new Map(spans.map((span) => [span.span_id, span]));
  const spanPositionById = new Map();
  let laneTop = 22;
  laneData.forEach((data) => {
    data.laneSpans.forEach((span) => {
      const position = data.layout.positions[span.span_id] || { row: 0 };
      const geometry = geometryById.get(span.span_id)
        || ComparisonState.traceSpanGeometry({
          span,
          index: spanIndexes.get(span.span_id) || 0,
          scale,
        });
      spanPositionById.set(span.span_id, {
        x: geometry.startPercent + geometry.widthPercent / 2,
        y: laneTop + position.row * 30 + 16,
      });
    });
    laneTop += data.trackHeight;
  });
  const canvasHeight = laneTop;
  const lanes = laneData.map((data) => `<div class="trace-lane"><div class="trace-lane-label">${escapeHtml(data.participant.label || data.participant.participant_id)}</div><div class="trace-track" style="height:${data.trackHeight}px">${data.spanMarkup || `<span class="fine-print">No calls</span>`}</div></div>`).join("");
  const traceLines = (trace.links || []).map((link) => {
    const source = spanPositionById.get(link.source_span_id);
    const target = spanPositionById.get(link.target_span_id);
    if (!source || !target) return "";
    return `<line class="trace-link-line" data-kind="${escapeHtml(link.kind)}" x1="${source.x}" y1="${source.y - 22}" x2="${target.x}" y2="${target.y - 22}" marker-end="url(#trace-arrow-${comparisonId}-${side})" />`;
  }).filter(Boolean).join("");
  const links = (trace.links || []).map((link) => {
    const source = spanById.get(link.source_span_id);
    const target = spanById.get(link.target_span_id);
    if (!source || !target) return "";
    const sourceParticipant = participants.find((participant) => participant.participant_id === source.participant_id);
    const targetParticipant = participants.find((participant) => participant.participant_id === target.participant_id);
    return `<div class="trace-link"><span>${escapeHtml(sourceParticipant?.label || source.participant_id)}: ${escapeHtml(source.operation)}</span><span class="trace-link-arrow">→</span><span>${escapeHtml(targetParticipant?.label || target.participant_id)}: ${escapeHtml(target.operation)}</span><span class="trace-link-kind">${escapeHtml(link.kind)}</span></div>`;
  }).filter(Boolean).join("");
  return `
    <article class="diff-trace-panel" data-side="${side}">
      ${includeHeading ? `<div class="diff-trace-heading"><h4>${side === "before" ? "Before" : "After"} · ${escapeHtml(displayStageLabel(run.stage))}</h4><span class="run-id" title="${escapeHtml(run.run_id)}">${escapeHtml(run.run_id)}</span></div>` : ""}
      <div class="trace-canvas"><div class="trace-content" style="height:${canvasHeight}px;min-width:${sequenceContentWidth}px"><div class="trace-axis">${axis}</div>${traceLines ? `<div class="trace-link-layer" aria-hidden="true"><svg viewBox="0 0 100 ${Math.max(1, canvasHeight - 22)}" preserveAspectRatio="none"><defs><marker id="trace-arrow-${comparisonId}-${side}" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5 z" /></marker></defs>${traceLines}</svg></div>` : ""}${lanes}</div></div>
      ${links ? `<div class="trace-links" aria-label="Trace links">${links}</div>` : ""}
    </article>
  `;
}

function layoutTraceSpans(spans, geometryById) {
  const rows = [];
  const positions = {};
  [...spans].sort(
    (a, b) => (geometryById.get(a.span_id)?.layoutStart || 0) - (geometryById.get(b.span_id)?.layoutStart || 0),
  ).forEach((span) => {
    const geometry = geometryById.get(span.span_id) || { layoutStart: 0, layoutEnd: 1 };
    const start = geometry.layoutStart;
    const end = geometry.layoutEnd;
    let row = rows.findIndex((lastEnd) => start >= lastEnd);
    if (row < 0) row = rows.length;
    rows[row] = end;
    positions[span.span_id] = { row };
  });
  return { rows: rows.length, positions };
}

function bindTraceSpanButtons() {
  elements.diffDialogGrid.querySelectorAll("[data-span-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.comparison.selectedSpanId = `${button.dataset.comparisonId}|${button.dataset.side}|${button.dataset.spanId}`;
      renderSpanInspector();
    });
  });
}

function renderSpanInspector() {
  const selection = state.comparison.selectedSpanId;
  if (!selection) {
    elements.diffSpanInspector.hidden = true;
    return;
  }
  const [comparisonId, side, spanId] = selection.split("|");
  const result = ComparisonState.comparisonResultById({
    history: state.comparison.history,
    failedTurn: state.comparison.failedTurn,
    comparisonId,
  });
  const run = result?.[side];
  const span = run?.trace?.spans?.find((item) => item.span_id === spanId);
  if (!span) {
    elements.diffSpanInspector.hidden = true;
    return;
  }
  const linkCount = (run.trace.links || []).filter((link) => link.source_span_id === spanId || link.target_span_id === spanId).length;
  const summary = [span.input_summary, span.output_summary].some((item) => Object.keys(item || {}).length)
    ? JSON.stringify({ input: span.input_summary, output: span.output_summary }, null, 2)
    : "No safe input/output fields were exposed for this span.";
  elements.diffSpanInspector.hidden = false;
  elements.diffSpanInspector.innerHTML = `
    <h4>${escapeHtml(span.component)}.${escapeHtml(span.operation)}()</h4>
    <p>${escapeHtml(span.summary)}</p>
    <div class="diff-span-meta"><span>${escapeHtml(side)}</span><span>${escapeHtml(span.status)}</span><span>${escapeHtml(formatDuration(span.duration_ms))}</span><span>${linkCount} link${linkCount === 1 ? "" : "s"}</span></div>
    <pre>${escapeHtml(summary)}</pre>
  `;
}

function formatDuration(value) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number < 1000 ? `${Math.round(number)} ms` : `${(number / 1000).toFixed(1)} s`;
}

async function loadHealth() {
  const health = await api("/api/health");
  const healthConfigChanged = Boolean(
    state.health
    && (
      state.health.provider !== health.provider
      || state.health.model !== health.model
    )
  );
  state.health = health;
  if (healthConfigChanged && (state.comparison.result || state.comparison.history.length || state.comparison.status === "running")) {
    invalidateComparison();
  }
  const mode = health.model_mode === "live" ? "Live · " : "API key missing · ";
  setConnection("connected", `${mode}${health.model}`);
  if (state.connectionLost && state.hasConnected) {
    showToast("Python server reconnected. Your transcript and materials are still here.");
  }
  state.connectionLost = false;
  state.hasConnected = true;
}

function openApiKeyDialog() {
  const supportsProviders = Boolean(state.health?.provider);
  const provider = state.health?.provider || "gemini";
  for (const option of elements.providerSelect.options) {
    option.disabled = !supportsProviders && option.value !== "gemini";
  }
  elements.providerSelect.value = provider;
  elements.modelInput.value = state.health?.model || DEFAULT_PROVIDER_MODELS[provider];
  elements.modelInput.disabled = !supportsProviders;
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
  const provider = elements.providerSelect.value;
  const model = elements.modelInput.value;
  const executionConfigChanged = (
    state.health?.provider !== provider
    || state.health?.model !== model.trim()
  );
  elements.saveApiKey.disabled = true;
  try {
    await api("/api/settings/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model, api_key: apiKey }),
    });
    closeApiKeyDialog();
    await loadHealth();
    if (executionConfigChanged && (state.comparison.result || state.comparison.history.length || state.comparison.status === "running")) {
      invalidateComparison();
    }
    showToast(`API key saved to .env. ${providerLabel(provider)} is ready.`);
  } catch (error) {
    showToast(`Could not save API key: ${error.message}`);
  } finally {
    elements.apiKeyInput.value = "";
    elements.saveApiKey.disabled = false;
  }
}

function providerLabel(provider) {
  return { gemini: "Gemini", openai: "OpenAI", anthropic: "Anthropic" }[provider] || provider;
}

async function reloadAgent() {
  if (state.reloadingAgent || pageExecutionIsActive()) return;
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
    elements.reloadAgent.disabled = state.thinking || state.clearing || state.materialsMutating;
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
  renderDiffButtonState();
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
  const materialsChanged = JSON.stringify(state.materials) !== JSON.stringify(data.materials);
  state.materials = data.materials;
  if (materialsChanged && (state.comparison.result || state.comparison.history.length || state.comparison.status === "running")) {
    invalidateComparison();
  }
  renderMaterials();
}

async function loadDefaultMaterials() {
  const data = await api(`/api/materials/defaults?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "POST" });
  const materialsChanged = JSON.stringify(state.materials) !== JSON.stringify(data.materials);
  state.materials = data.materials;
  if (materialsChanged && (state.comparison.result || state.comparison.history.length || state.comparison.status === "running")) {
    invalidateComparison();
  }
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
    syncMaterialControls();
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
  syncMaterialControls();
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
  if (!file || pageExecutionIsActive()) return;
  const form = new FormData();
  form.append("workspace_id", state.workspaceId);
  form.append("kind", elements.materialKind.value);
  form.append("file", file);
  const fileButton = elements.materialFile.closest(".file-button");
  setMaterialsMutating(true);
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
    setMaterialsMutating(false);
    fileButton.classList.remove("is-loading");
    elements.uploadLabel.textContent = "Upload file";
    elements.materialFile.value = "";
  }
}

async function pasteMaterial(event) {
  event.preventDefault();
  if (pageExecutionIsActive()) return;
  setMaterialsMutating(true);
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
  } finally {
    setMaterialsMutating(false);
  }
}

async function addWebSource(event) {
  event.preventDefault();
  const url = elements.webSourceUrl.value.trim();
  if (!url || pageExecutionIsActive()) return;
  setMaterialsMutating(true);
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
  } finally {
    setMaterialsMutating(false);
  }
}

async function deleteMaterial(materialId) {
  if (pageExecutionIsActive()) return;
  setMaterialsMutating(true);
  try {
    await api(`/api/materials/${encodeURIComponent(materialId)}?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "DELETE" });
    await loadMaterials();
    showToast("Material deleted.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setMaterialsMutating(false);
  }
}

async function clearJobWorkspace() {
  if (pageExecutionIsActive()) return;
  if (!window.confirm("Clear every profile, JD, and source from this local workspace?")) return;
  setClearing(true);
  try {
    await api(`/api/materials?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "DELETE" });
    localStorage.setItem("harness.materialsInitialized", "true");
    localStorage.setItem("harness.defaultsStage", String(Math.max(1, currentStageNumber())));
    await loadMaterials();
    state.chatRetry = null;
    showToast("Job workspace cleared.");
  } catch (error) {
    showToast(error.message);
  } finally {
    setClearing(false);
  }
}

async function clearConversation() {
  if (pageExecutionIsActive()) return;
  setClearing(true);
  saveMessages([]);
  renderTranscript();
  clearInspector();
  if (
    state.comparison.result
    || state.comparison.history.length
    || state.comparison.status !== "ready"
  ) {
    invalidateComparison();
  } else {
    state.comparison.serverStateReady = false;
  }
  try {
    await api(
      `/api/task-state?workspace_id=${encodeURIComponent(state.workspaceId)}&stage_id=${encodeURIComponent(state.stage)}&session_id=${encodeURIComponent(state.sessionId)}`,
      { method: "DELETE" },
    );
    state.chatRetry = null;
    showToast("Conversation and managed task state cleared.");
  } catch (error) {
    showToast(`Conversation cleared, but task state could not be reset: ${error.message}`);
  } finally {
    setClearing(false);
  }
}

async function restoreExampleMaterials() {
  if (pageExecutionIsActive()) return;
  setMaterialsMutating(true);
  try {
    await loadDefaultMaterials();
  } catch (error) {
    showToast(error.message);
  } finally {
    setMaterialsMutating(false);
  }
}

function renderCapabilityCard() {
  const stage = currentStage();
  if (!stage) return;
  elements.capabilityCard.innerHTML = `
    <div class="capability-top">
      <button class="capability-toggle" type="button" aria-expanded="true" aria-controls="capability-body">
        <span class="capability-toggle-copy">
          <span class="eyebrow">WHEN THIS LAB IS COMPLETE</span>
          <span class="capability-title">Now your Agent can…</span>
        </span>
        <span class="capability-toggle-icon" aria-hidden="true">⌃</span>
      </button>
    </div>
    <div id="capability-body" class="capability-body">
      <ul class="capability-list">${stage.now_you_can.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      <p class="limitation"><strong>Still cannot:</strong> ${escapeHtml(stage.still_cannot)}</p>
    </div>
  `;
  elements.exampleActions.innerHTML = stage.examples.length
    ? `<div class="example-row">${stage.examples.map((example, index) => `<button class="example-button" type="button" data-example="${index}">Try example ${index + 1} · ${escapeHtml(example.id.replaceAll("_", " "))}</button>`).join("")}</div>`
    : "";

  const capabilityToggle = elements.capabilityCard.querySelector(".capability-toggle");
  const capabilityBody = elements.capabilityCard.querySelector(".capability-body");
  capabilityToggle.addEventListener("click", () => {
    const isExpanded = capabilityToggle.getAttribute("aria-expanded") === "true";
    capabilityToggle.setAttribute("aria-expanded", String(!isExpanded));
    capabilityBody.hidden = isExpanded;
  });
  elements.exampleActions.querySelectorAll("[data-example]").forEach((button) => {
    button.addEventListener("click", () => {
      elements.messageInput.value = stage.examples[Number(button.dataset.example)].prompt;
      elements.messageInput.focus();
      renderDiffButtonState();
    });
  });
  const stageNumber = Number(stage.id.split("_")[1]);
  elements.runEval.hidden = stageNumber < 5 || !stage.available;
}

async function runEvalSuite() {
  if (pageExecutionIsActive()) return;
  setThinking(true);
  clearInspector(true);
  try {
    const response = await api("/api/evals/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: state.stage, workspace_id: state.workspaceId }),
    });
    renderInspector(response);
    if (response.status === "ok") {
      const summary = response.summary;
      showToast(`Eval complete: ${summary.passed}/${summary.total} passed, ${summary.failed} failed. Inspect the run for details.`);
    } else {
      showToast(response.error?.message || "Eval failed.");
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    setThinking(false);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.comparison.open) {
    await runComparison();
    return;
  }
  const content = elements.messageInput.value.trim();
  if (!content || state.thinking || state.clearing || state.materialsMutating) return;
  const requestPlan = ComparisonState.chatRequestPlan({
    retry: state.chatRetry,
    content,
    stage: state.stage,
    backend: state.backend,
    provider: state.health?.provider,
    model: state.health?.model,
  });
  if (requestPlan.blocked) {
    showToast("Retry the interrupted request unchanged, or clear that Lab conversation before sending a new message.");
    return;
  }
  const messages = loadMessages();
  messages.push({ role: "user", content });
  const requestRecord = requestPlan.replaying
    ? state.chatRetry
    : {
        requestId: createId("chat_request"),
        content,
        stage: state.stage,
        backend: state.backend,
        provider: state.health?.provider,
        model: state.health?.model,
        messages: requestMessages(messages),
      };
  saveMessages(messages);
  elements.messageInput.value = "";
  renderTranscript();
  setThinking(true);
  clearInspector(true);
  state.chatRetry = requestRecord;
  let locallyCommitted = false;

  try {
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stage: state.stage,
        backend: state.backend,
        session_id: state.sessionId,
        workspace_id: state.workspaceId,
        request_id: requestRecord.requestId,
        messages: requestRecord.messages,
      }),
    });
    if (currentStageNumber() >= 3) await loadMaterials();
    if (response.status === "ok") {
      messages.push({ role: "assistant", content: response.assistant_message });
      saveMessages(messages);
    } else {
      removePendingUserTurn(messages, content);
      messages.push({ role: "error", content: response.error?.message || "The Lab run failed.", error: response.error });
      saveMessages(messages);
      restoreDraft(content);
    }
    locallyCommitted = true;
    state.chatRetry = null;
    acknowledgeChatRequest(requestRecord);
    renderTranscript();
    renderInspector(response);
  } catch (error) {
    if (!locallyCommitted) {
      removePendingUserTurn(messages, content);
      messages.push({ role: "error", content: error.message });
      saveMessages(messages);
      restoreDraft(content);
      renderTranscript();
      setConnection("reloading", "Reconnecting…");
      state.connectionLost = true;
    } else {
      showToast("The response was saved, but the inspector could not refresh.");
    }
  } finally {
    setThinking(false);
  }
}

function removePendingUserTurn(messages, content) {
  const index = messages.findLastIndex(
    (message) => message.role === "user" && message.content === content,
  );
  if (index >= 0) messages.splice(index, 1);
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

    if (isMarkdownTableStart(lines, index)) {
      const headers = splitMarkdownTableRow(lines[index]);
      const delimiters = splitMarkdownTableRow(lines[index + 1]);
      const alignments = delimiters.map((delimiter) => {
        const value = delimiter.trim();
        if (value.startsWith(":") && value.endsWith(":")) return "center";
        if (value.endsWith(":")) return "right";
        return "left";
      });
      const renderCells = (cells, tag) => headers.map((_, cellIndex) => {
        const alignment = alignments[cellIndex];
        const value = cells[cellIndex] ?? "";
        return `<${tag} class="align-${alignment}">${renderInlineMarkdown(value.trim())}</${tag}>`;
      }).join("");
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        rows.push(`<tr>${renderCells(splitMarkdownTableRow(lines[index]), "td")}</tr>`);
        index += 1;
      }
      blocks.push(
        `<div class="markdown-table-wrap"><table><thead><tr>${renderCells(headers, "th")}</tr></thead>`
        + `<tbody>${rows.join("")}</tbody></table></div>`,
      );
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
    while (
      index < lines.length
      && lines[index].trim()
      && !isMarkdownBlockStart(lines[index])
      && !isMarkdownTableStart(lines, index)
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n"))}</p>`);
  }

  return blocks.join("");
}

function splitMarkdownTableRow(line) {
  const source = String(line ?? "").trim();
  const cells = [];
  let cell = "";
  let codeDelimiterLength = 0;

  for (let index = 0; index < source.length;) {
    if (source[index] === "\\") {
      const slashStart = index;
      while (source[index] === "\\") index += 1;
      const slashCount = index - slashStart;
      const escapedCharacter = source[index];
      if (slashCount % 2 === 1 && escapedCharacter === "|") {
        cell += "\\".repeat(slashCount - 1) + escapedCharacter;
        index += 1;
      } else if (slashCount % 2 === 1 && escapedCharacter === "`") {
        cell += "\\".repeat(slashCount) + escapedCharacter;
        index += 1;
      } else {
        cell += "\\".repeat(slashCount);
      }
      continue;
    }

    if (source[index] === "`") {
      const delimiterStart = index;
      while (source[index] === "`") index += 1;
      const delimiterLength = index - delimiterStart;
      if (codeDelimiterLength === delimiterLength) codeDelimiterLength = 0;
      else if (codeDelimiterLength === 0) codeDelimiterLength = delimiterLength;
      cell += "`".repeat(delimiterLength);
      continue;
    }

    if (source[index] === "|" && codeDelimiterLength === 0) {
      cells.push(cell);
      cell = "";
      index += 1;
      continue;
    }

    cell += source[index];
    index += 1;
  }

  cells.push(cell);
  if (source.startsWith("|")) cells.shift();
  if (source.endsWith("|") && cells.at(-1) === "") cells.pop();
  return cells;
}

function isMarkdownTableStart(lines, index) {
  if (index + 1 >= lines.length || !lines[index].includes("|")) return false;
  const headers = splitMarkdownTableRow(lines[index]);
  const delimiters = splitMarkdownTableRow(lines[index + 1]);
  return headers.length > 1
    && headers.length === delimiters.length
    && delimiters.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
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

const MODEL_IO_KEYS = new Set([
  "system_prompt",
  "user_request",
  "provider_input_mode",
  "actual_provider_input",
  "raw_model_output",
  "validated_output",
]);

function hasModelIo(details = {}) {
  return [...MODEL_IO_KEYS].some((key) => details[key] !== undefined);
}

function renderModelIoFields(details = {}, comparison = {}) {
  const providerInputLabels = {
    reconstructed_lab_1_boundary: "RECONSTRUCTED PROVIDER INPUT (LAB 1 BOUNDARY)",
    normalized_tool_flow: "NORMALIZED PROVIDER INPUT",
  };
  const providerInputLabel = providerInputLabels[details.provider_input_mode]
    || "ACTUAL PROVIDER INPUT";
  const fields = [
    ["SYSTEM PROMPT", "system_prompt"],
    ["USER REQUEST", "user_request"],
    [providerInputLabel, "actual_provider_input"],
    ["RAW MODEL OUTPUT", "raw_model_output"],
    ["VALIDATED OUTPUT", "validated_output"],
  ];
  return `
    <div class="model-io-meta">${escapeHtml(details.provider || "provider unknown")} · ${escapeHtml(details.model || "model unknown")}</div>
    <div class="model-io-fields">
      ${fields
        .filter(([, key]) => key === null || details[key] !== undefined)
        .map(([label, key]) => {
          const fieldComparison = (comparison.fieldComparisons || []).find((field) => field.key === key);
          const statusLabel = fieldComparison
            ? fieldComparison.status === "same"
              ? "SAME"
              : fieldComparison.output
                ? "DIFFERENT OUTPUT"
                : "CHANGED"
            : "";
          const excerpt = fieldComparison?.status === "different"
            && fieldComparison.input
            ? fieldComparison[`${comparison.side}_excerpt`]
            : "";
          const note = fieldComparison?.status === "different"
            && fieldComparison.input
            ? excerpt
              ? `Only on this side: ${excerpt}`
              : "The other side adds content at this position."
            : "";
          return `
          <section class="model-io-field">
            <div class="model-io-field-heading">
              <div class="section-label">${label}</div>
              ${statusLabel ? `<span class="model-io-diff-status" data-status="${fieldComparison.status}" data-output="${fieldComparison.output}">${statusLabel}</span>` : ""}
            </div>
            ${note ? `<div class="model-io-diff-note">${escapeHtml(note)}</div>` : ""}
            <pre>${escapeHtml(String(details[key]))}</pre>
          </section>
        `;
        }).join("")}
    </div>
  `;
}

function renderEventDetails(details = {}, openModelIo = true, comparison = {}) {
  if (!Object.keys(details).length) return "";
  if (!hasModelIo(details)) {
    return `<details class="event-details"><summary>Input / output summary</summary><pre>${escapeHtml(JSON.stringify(details, null, 2))}</pre></details>`;
  }
  const metadata = Object.fromEntries(
    Object.entries(details).filter(([key]) => !MODEL_IO_KEYS.has(key)),
  );
  return `
    <details class="event-details model-io-details" ${openModelIo ? "open" : ""}>
      <summary>Prompt &amp; model I/O</summary>
      ${renderModelIoFields(details, comparison)}
      ${Object.keys(metadata).length ? `<section class="model-io-field"><div class="section-label">CALL METADATA</div><pre>${escapeHtml(JSON.stringify(metadata, null, 2))}</pre></section>` : ""}
    </details>
  `;
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
        ${renderEventDetails(event.details)}
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
    elements.artifacts.innerHTML = `<div class="section-label">Artifacts</div>${response.artifacts.map((artifact) => `<a class="artifact-link" target="_blank" rel="noopener noreferrer" href="${artifactHref(artifact.path)}">↗ ${escapeHtml(artifact.label)}</a>`).join("")}`;
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
  try {
    const messages = JSON.parse(localStorage.getItem(messageKey()) || "[]");
    if (!Array.isArray(messages)) return [];
    const migrated = messages.filter((message) => !isLegacyEvalTranscriptMessage(message));
    if (migrated.length !== messages.length) {
      saveMessages(migrated);
    }
    return migrated;
  }
  catch { return []; }
}

function isLegacyEvalTranscriptMessage(message) {
  return message?.role === "assistant"
    && /^Eval complete: \d+\/\d+ passed, \d+ failed\. Inspect each task to find the first failure point\.$/.test(String(message.content));
}

function saveMessages(messages) {
  const serialized = JSON.stringify(messages);
  const changed = localStorage.getItem(messageKey()) !== serialized;
  localStorage.setItem(messageKey(), serialized);
  if (changed && (state.comparison.result || state.comparison.history.length || state.comparison.status === "running")) {
    invalidateComparison();
  }
}

function artifactHref(path) {
  return `/api/artifacts/${String(path).split("/").map(encodeURIComponent).join("/")}`;
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

function acknowledgeChatRequest(request) {
  const query = new URLSearchParams({
    workspace_id: state.workspaceId,
    session_id: state.sessionId,
    stage_id: request.stage,
    request_id: request.requestId,
  });
  void api(`/api/chat-request?${query}`, { method: "DELETE" }).catch(() => {});
}

function acknowledgeComparisonRequest(requestId, stage) {
  const query = new URLSearchParams({
    workspace_id: state.workspaceId,
    session_id: state.sessionId,
    current_stage: stage,
    request_id: requestId,
  });
  void api(`/api/comparison-request?${query}`, { method: "DELETE" }).catch(() => {});
}

function restoreDraft(content) {
  if (!elements.messageInput.value) elements.messageInput.value = content;
}

function messageKey() { return `harness.messages.${state.stage}`; }
function currentStage() { return state.stages.find((stage) => stage.id === state.stage); }
function displayStageLabel(stageId) {
  const match = String(stageId || "").match(/(?:lab[_ ]?)?(\d+)/i);
  return match ? `Lab ${Number(match[1])}` : String(stageId || "");
}

function currentStageNumber() { return Number(state.stage.split("_")[1]); }

function pageExecutionIsActive() {
  return ComparisonState.executionIsActive({
    thinking: state.thinking,
    clearing: state.clearing,
    materialsMutating: state.materialsMutating,
    comparisonStatus: state.comparison.status,
  });
}

function syncMaterialControls() {
  const disabled = pageExecutionIsActive();
  elements.materialKind.disabled = disabled;
  elements.materialFile.disabled = disabled;
  elements.openPaste.disabled = disabled;
  elements.webSourceUrl.disabled = disabled;
  elements.addWebSource.disabled = disabled;
  elements.pasteName.disabled = disabled;
  elements.pasteText.disabled = disabled;
  elements.pasteForm.querySelector("button[type='submit']").disabled = disabled;
  document.querySelectorAll(".delete-material, #restore-example-materials").forEach((button) => {
    button.disabled = disabled;
  });
}

function setMaterialsMutating(value) {
  state.materialsMutating = value;
  document.body.dataset.materialsMutating = String(value);
  elements.sendButton.disabled = value || state.thinking || state.clearing || state.comparison.status === "running";
  elements.runEval.disabled = value || state.thinking || state.clearing;
  elements.reloadAgent.disabled = value || state.thinking || state.clearing || state.reloadingAgent;
  elements.messageInput.disabled = value || state.thinking || state.clearing;
  elements.clearMaterials.disabled = value || state.thinking || state.clearing || state.comparison.status === "running";
  elements.clearChat.disabled = value || state.thinking || state.clearing || state.comparison.status === "running";
  elements.rerunDiff.disabled = value || state.clearing || state.comparison.status === "running";
  elements.resetDiff.disabled = value || state.clearing || state.comparison.status === "running";
  syncMaterialControls();
  renderDiffButtonState();
}

function setThinking(value) {
  state.thinking = value;
  document.body.dataset.thinking = String(value);
  elements.sendButton.disabled = value || state.clearing || state.materialsMutating;
  elements.runEval.disabled = value || state.clearing || state.materialsMutating;
  elements.reloadAgent.disabled = value || state.clearing || state.materialsMutating || state.reloadingAgent;
  elements.messageInput.disabled = value || state.clearing || state.materialsMutating;
  elements.clearMaterials.disabled = value || state.clearing || state.materialsMutating || state.comparison.status === "running";
  elements.clearChat.disabled = value || state.clearing || state.materialsMutating || state.comparison.status === "running";
  syncMaterialControls();
  renderDiffButtonState();
}

function setClearing(value) {
  state.clearing = value;
  document.body.dataset.clearing = String(value);
  elements.sendButton.disabled = value || state.thinking || state.materialsMutating || state.comparison.status === "running";
  elements.runEval.disabled = value || state.thinking || state.materialsMutating;
  elements.reloadAgent.disabled = value || state.thinking || state.materialsMutating || state.reloadingAgent;
  elements.messageInput.disabled = value || state.thinking || state.materialsMutating;
  elements.clearMaterials.disabled = value || state.thinking || state.materialsMutating || state.comparison.status === "running";
  elements.clearChat.disabled = value || state.thinking || state.materialsMutating || state.comparison.status === "running";
  elements.rerunDiff.disabled = value || state.materialsMutating || state.comparison.status === "running";
  elements.resetDiff.disabled = value || state.materialsMutating || state.comparison.status === "running";
  syncMaterialControls();
  renderDiffButtonState();
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

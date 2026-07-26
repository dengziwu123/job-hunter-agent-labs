(function exposeComparisonState(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ComparisonState = api;
  }
})(typeof globalThis === "undefined" ? window : globalThis, function buildComparisonState() {
  const MAX_COMPARISON_TURNS = 80;

  function freshComparisonState({ open = false, useTranscriptPrompt = true } = {}) {
    return {
      open,
      status: "ready",
      prompt: "",
      messages: [],
      result: null,
      history: [],
      error: null,
      selectedSpanId: null,
      replacingLatest: false,
      failedRequestWasRerun: false,
      failedTurn: null,
      serverStateReady: false,
      retryRequestId: null,
      useTranscriptPrompt,
    };
  }

  function comparisonHistoryPlan({
    history,
    useStoredPrompt,
    result,
    failedRequestWasRerun,
    failedTurn,
  }) {
    if (useStoredPrompt && failedTurn) {
      return {
        replaceLatest: failedTurn.replacedLatest,
        historyBase: history.slice(),
      };
    }
    const replaceLatest = Boolean(
      useStoredPrompt
      && (result !== null || failedRequestWasRerun),
    );
    return {
      replaceLatest,
      historyBase: replaceLatest ? history.slice(0, -1) : history.slice(),
    };
  }

  function executionIsActive({
    thinking,
    clearing = false,
    materialsMutating = false,
    comparisonStatus,
  }) {
    return Boolean(thinking || clearing || materialsMutating || comparisonStatus === "running");
  }

  function comparisonResultPlan({
    historyBase,
    prompt,
    result,
    supportsHistory,
    replacedLatest,
  }) {
    const beforeOk = result.before.status === "ok";
    const afterOk = result.after.status === "ok";
    const fullyFailed = !beforeOk && !afterOk;
    const turn = { prompt, result };
    return {
      status: beforeOk && afterOk ? "success" : fullyFailed ? "full_failure" : "partial_failure",
      history: fullyFailed && supportsHistory
        ? historyBase.slice()
        : supportsHistory
          ? [...historyBase, turn].slice(-MAX_COMPARISON_TURNS)
          : [turn],
      failedTurn: fullyFailed && supportsHistory
        ? { ...turn, replacedLatest }
        : null,
    };
  }

  function comparisonResultById({ history, failedTurn, comparisonId }) {
    return history.find(
      (turn) => turn.result?.comparison_id === comparisonId,
    )?.result
      || (
        failedTurn?.result?.comparison_id === comparisonId
          ? failedTurn.result
          : null
      );
  }

  function chatRequestPlan({
    retry,
    content,
    stage,
    backend,
    provider,
    model,
  }) {
    if (!retry) return { blocked: false, replaying: false };
    const matches = (
      retry.content === content
      && retry.stage === stage
      && retry.backend === backend
      && retry.provider === provider
      && retry.model === model
    );
    return { blocked: !matches, replaying: matches };
  }

  return {
    freshComparisonState,
    comparisonHistoryPlan,
    comparisonResultPlan,
    comparisonResultById,
    chatRequestPlan,
    executionIsActive,
  };
});

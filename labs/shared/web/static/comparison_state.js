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

  function comparisonTraceScale({ before = {}, after = {} }) {
    const beforeSpans = before.trace?.spans || [];
    const afterSpans = after.trace?.spans || [];
    const spans = [...beforeSpans, ...afterSpans];
    const hasCompleteTiming = spans.length > 0 && spans.every(
      (span) => Number.isFinite(span.start_offset_ms)
        && Number.isFinite(span.duration_ms)
        && span.duration_ms > 0,
    );
    if (!hasCompleteTiming) {
      return {
        mode: "sequence",
        total: Math.max(1, beforeSpans.length, afterSpans.length),
      };
    }
    return {
      mode: "time",
      total: Math.max(
        1,
        ...spans.map((span) => span.start_offset_ms + span.duration_ms),
      ),
    };
  }

  function traceSpanGeometry({ span = {}, index = 0, scale }) {
    const total = Math.max(1, scale?.total || 1);
    if (
      scale?.mode === "time"
      && Number.isFinite(span.start_offset_ms)
      && Number.isFinite(span.duration_ms)
      && span.duration_ms > 0
    ) {
      return {
        startPercent: Math.max(0, span.start_offset_ms) / total * 100,
        widthPercent: Math.max(1.4, span.duration_ms / total * 100),
        layoutStart: Math.max(0, span.start_offset_ms),
        layoutEnd: Math.max(0, span.start_offset_ms) + span.duration_ms,
      };
    }
    return {
      startPercent: Math.max(0, index) / total * 100,
      widthPercent: 1 / total * 100,
      layoutStart: Math.max(0, index),
      layoutEnd: Math.max(0, index) + 1,
    };
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

  function comparisonEvidenceSteps({
    events = [],
    trace = {},
    delta = {},
    side = "after",
  } = {}) {
    const spans = Array.isArray(trace?.spans) ? trace.spans : [];
    const added = occurrenceKeys(delta.added_span_keys);
    const removed = occurrenceKeys(delta.removed_span_keys);
    const changed = changedEvidenceKeys(delta);
    const occurrences = {};
    return events.map((event, index) => {
      const span = spans[index] || {};
      const semanticKey = span.semantic_key || fallbackSemanticKey(event);
      const occurrence = occurrences[semanticKey] || 0;
      occurrences[semanticKey] = occurrence + 1;
      const occurrenceKey = `${semanticKey}:${occurrence}`;
      let change = "unchanged";
      if (side === "after" && added.has(occurrenceKey)) change = "added";
      else if (side === "before" && removed.has(occurrenceKey)) change = "removed";
      else if (changed.occurrences.has(occurrenceKey) || changed.semanticKeys.has(semanticKey)) change = "changed";
      return {
        sequence: event.sequence ?? index + 1,
        type: event.type || span.kind || "event",
        status: event.status || span.status || "completed",
        component: event.component || span.component || "unknown",
        operation: event.operation || span.operation || "run",
        summary: event.summary || span.summary || "",
        duration_ms: event.duration_ms,
        details: event.details || {},
        semantic_key: semanticKey,
        occurrence,
        change,
      };
    });
  }

  function comparisonEvidenceRows({
    before = {},
    after = {},
    delta = {},
  } = {}) {
    const beforeSteps = comparisonEvidenceSteps({
      events: before.events,
      trace: before.trace,
      delta,
      side: "before",
    });
    const afterSteps = comparisonEvidenceSteps({
      events: after.events,
      trace: after.trace,
      delta,
      side: "after",
    });
    const beforeKeys = beforeSteps.map(evidenceOccurrenceKey);
    const afterKeys = afterSteps.map(evidenceOccurrenceKey);
    const lengths = longestCommonSubsequenceLengths(beforeKeys, afterKeys);
    const rows = [];
    let beforeIndex = 0;
    let afterIndex = 0;
    while (beforeIndex < beforeSteps.length && afterIndex < afterSteps.length) {
      if (beforeKeys[beforeIndex] === afterKeys[afterIndex]) {
        rows.push(pairEvidenceSteps(beforeSteps[beforeIndex], afterSteps[afterIndex]));
        beforeIndex += 1;
        afterIndex += 1;
      } else if (lengths[beforeIndex + 1][afterIndex] >= lengths[beforeIndex][afterIndex + 1]) {
        rows.push({ before: beforeSteps[beforeIndex], after: null });
        beforeIndex += 1;
      } else {
        rows.push({ before: null, after: afterSteps[afterIndex] });
        afterIndex += 1;
      }
    }
    while (beforeIndex < beforeSteps.length) {
      rows.push({ before: beforeSteps[beforeIndex], after: null });
      beforeIndex += 1;
    }
    while (afterIndex < afterSteps.length) {
      rows.push({ before: null, after: afterSteps[afterIndex] });
      afterIndex += 1;
    }
    return rows;
  }

  function pairEvidenceSteps(before, after) {
    const fieldComparisons = evidenceFieldComparisons(before.details, after.details);
    const changedFields = fieldComparisons
      .filter((field) => field.input && field.status === "different")
      .map((field) => field.key);
    const changed = (
      changedFields.length > 0
      || before.change === "changed"
      || after.change === "changed"
    );
    return {
      before: {
        ...before,
        change: changed ? "changed" : before.change,
        changed_fields: changedFields,
        io_field_comparisons: fieldComparisons,
      },
      after: {
        ...after,
        change: changed ? "changed" : after.change,
        changed_fields: changedFields,
        io_field_comparisons: fieldComparisons,
      },
    };
  }

  function evidenceFieldComparisons(beforeDetails = {}, afterDetails = {}) {
    const inputFields = new Set([
      "system_prompt",
      "user_request",
      "request",
      "messages",
      "input",
      "tool_args",
      "provider_input",
      "actual_provider_input",
      "reconstructed_provider_input",
      "provider_input_mode",
    ]);
    const outputFields = new Set(["raw_model_output", "validated_output"]);
    const fields = [...new Set([...inputFields, ...outputFields])];
    return fields
      .filter((key) => (
        Object.prototype.hasOwnProperty.call(beforeDetails, key)
        || Object.prototype.hasOwnProperty.call(afterDetails, key)
      ))
      .map((key) => {
        const beforeValue = comparableFieldValue(beforeDetails[key]);
        const afterValue = comparableFieldValue(afterDetails[key]);
        const status = beforeValue === afterValue ? "same" : "different";
        const excerpts = status === "different"
          ? differingExcerpts(beforeValue, afterValue)
          : { before: "", after: "" };
        return {
          key,
          status,
          input: inputFields.has(key),
          output: outputFields.has(key),
          before_excerpt: excerpts.before,
          after_excerpt: excerpts.after,
        };
      });
  }

  function comparableFieldValue(value) {
    return typeof value === "string" ? value : stableStringify(value);
  }

  function differingExcerpts(before, after) {
    const beforeText = String(before ?? "");
    const afterText = String(after ?? "");
    let prefix = 0;
    while (
      prefix < beforeText.length
      && prefix < afterText.length
      && beforeText[prefix] === afterText[prefix]
    ) {
      prefix += 1;
    }
    let suffix = 0;
    while (
      suffix < beforeText.length - prefix
      && suffix < afterText.length - prefix
      && beforeText[beforeText.length - suffix - 1] === afterText[afterText.length - suffix - 1]
    ) {
      suffix += 1;
    }
    return {
      before: clipDifference(beforeText.slice(prefix, beforeText.length - suffix)),
      after: clipDifference(afterText.slice(prefix, afterText.length - suffix)),
    };
  }

  function clipDifference(value) {
    const compact = String(value || "").replace(/\\r?\\n/g, " ").replace(/\s+/g, " ").trim();
    if (compact.length <= 220) return compact;
    return `${compact.slice(0, 217)}…`;
  }

  function stableStringify(value) {
    if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function evidenceOccurrenceKey(step) {
    return `${step.semantic_key}:${step.occurrence}`;
  }

  function longestCommonSubsequenceLengths(beforeKeys, afterKeys) {
    const lengths = Array.from(
      { length: beforeKeys.length + 1 },
      () => Array(afterKeys.length + 1).fill(0),
    );
    for (let beforeIndex = beforeKeys.length - 1; beforeIndex >= 0; beforeIndex -= 1) {
      for (let afterIndex = afterKeys.length - 1; afterIndex >= 0; afterIndex -= 1) {
        lengths[beforeIndex][afterIndex] = beforeKeys[beforeIndex] === afterKeys[afterIndex]
          ? lengths[beforeIndex + 1][afterIndex + 1] + 1
          : Math.max(lengths[beforeIndex + 1][afterIndex], lengths[beforeIndex][afterIndex + 1]);
      }
    }
    return lengths;
  }

  function changedEvidenceKeys(delta = {}) {
    const changeLists = [
      delta.changed_spans,
      delta.ownership_changes,
      delta.input_contract_changes,
      delta.output_contract_changes,
      delta.context_changes,
    ];
    const occurrences = new Set();
    const semanticKeys = new Set();
    changeLists.flatMap((items) => items || []).forEach((item) => {
      if (Number.isInteger(item.occurrence)) {
        occurrences.add(`${item.semantic_key}:${item.occurrence}`);
      } else {
        semanticKeys.add(item.semantic_key);
      }
    });
    return { occurrences, semanticKeys };
  }

  function occurrenceKeys(items = []) {
    return new Set(
      items.map((item) => `${item.semantic_key}:${item.occurrence}`),
    );
  }

  function fallbackSemanticKey(event = {}) {
    return [event.type || "event", event.component || "unknown", event.operation || "run"]
      .map((value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.+|\.+$/g, ""))
      .join(".");
  }

  return {
    freshComparisonState,
    comparisonHistoryPlan,
    comparisonResultPlan,
    comparisonResultById,
    chatRequestPlan,
    comparisonEvidenceSteps,
    comparisonEvidenceRows,
    comparisonTraceScale,
    traceSpanGeometry,
    executionIsActive,
  };
});

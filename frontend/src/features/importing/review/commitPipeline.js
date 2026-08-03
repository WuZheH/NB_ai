import {
  resetCommitPipeline,
  updateCommitPhase,
  updatePhaseEntry,
} from "./reviewState.js";

const noop = () => {};

function phaseError(setState, phase, result) {
  setState(state => ({
    ...state,
    commitLoading: false,
    commitPhase: updatePhaseEntry(state.commitPhase, phase, "error", result),
  }));
}

export async function commitReviewedObjectsPhase({
  jobId,
  api,
  setState,
  updateSafety = noop,
  onRefresh,
}) {
  setState(state => updateCommitPhase(state, "objects", "running"));
  let result;
  try {
    result = await api.commitReviewedObjects(jobId);
    updateSafety(result);
  } catch (error) {
    const failure = { error: error.message };
    setState(state => ({
      ...state,
      commitLoading: false,
      saveResult: failure,
      saveStatus: "error",
      commitPhase: updatePhaseEntry(state.commitPhase, "objects", "error", failure),
    }));
    return { status: "error", phase: "objects", result: failure };
  }

  const status = result.status;
  if (status !== "committed" && status !== "ok" && status !== "already_committed") {
    setState(state => ({
      ...state,
      commitLoading: false,
      saveResult: result,
      saveStatus: "error",
      commitPhase: updatePhaseEntry(state.commitPhase, "objects", "error", result),
    }));
    return { status: "error", phase: "objects", result };
  }

  const phaseStatus = status === "already_committed" ? "already_committed" : "ok";
  setState(state => ({
    ...state,
    commitLoading: false,
    saveResult: result,
    saveStatus: "committed",
    commitPhase: updatePhaseEntry(state.commitPhase, "objects", phaseStatus, result),
  }));
  if (onRefresh) onRefresh();
  return { status: phaseStatus, phase: "objects", result };
}

export async function continueCommitAfterRemap(options) {
  options.setState(state => ({
    ...state,
    commitLoading: true,
    confirmRemapFailed: false,
    saveStatus: "",
    saveResult: null,
  }));
  return commitReviewedObjectsPhase(options);
}

export async function runCommitPipeline({
  jobId,
  api,
  setState,
  updateSafety = noop,
  onRefresh,
}) {
  const normalizedJobId = String(jobId || "").trim();
  if (!normalizedJobId) {
    setState(state => ({
      ...state,
      saveStatus: "error",
      saveResult: { error: "请输入 import_job_id。" },
    }));
    return { status: "error", phase: "input" };
  }

  setState(resetCommitPipeline);
  setState(state => updateCommitPhase(state, "paper", "running"));

  let paperResult;
  try {
    paperResult = await api.commitPaper(normalizedJobId);
    updateSafety(paperResult);
  } catch (error) {
    const failure = { error: error.message };
    phaseError(setState, "paper", failure);
    return { status: "error", phase: "paper", result: failure };
  }

  if (paperResult.status === "committed" || paperResult.status === "ok") {
    setState(state => updateCommitPhase(state, "paper", "ok", paperResult));
  } else if (paperResult.status === "already_committed") {
    setState(state => updateCommitPhase(state, "paper", "already_committed", paperResult));
  } else {
    phaseError(setState, "paper", paperResult);
    return { status: "error", phase: "paper", result: paperResult };
  }

  setState(state => updateCommitPhase(state, "remap", "running"));
  let remapResult;
  try {
    remapResult = await api.previewReviewedObjectRemap(normalizedJobId);
    updateSafety(remapResult);
  } catch (error) {
    const failure = { error: error.message };
    phaseError(setState, "remap", failure);
    return { status: "error", phase: "remap", result: failure };
  }

  const failedCount = remapResult.summary?.failed || 0;
  if (remapResult.status === "ok" && failedCount > 0) {
    setState(state => updateCommitPhase(state, "remap", "warning", remapResult));
    setState(state => ({
      ...state,
      remapPreview: remapResult,
      commitLoading: false,
      confirmRemapFailed: true,
    }));
    return { status: "warning", phase: "remap", result: remapResult };
  }
  if (remapResult.status !== "ok") {
    setState(state => ({
      ...state,
      commitLoading: false,
      remapPreview: remapResult,
      commitPhase: updatePhaseEntry(state.commitPhase, "remap", "error", remapResult),
    }));
    return { status: "error", phase: "remap", result: remapResult };
  }

  setState(state => ({ ...state, remapPreview: remapResult }));
  setState(state => updateCommitPhase(state, "remap", "ok", remapResult));
  return commitReviewedObjectsPhase({
    jobId: normalizedJobId,
    api,
    setState,
    updateSafety,
    onRefresh,
  });
}

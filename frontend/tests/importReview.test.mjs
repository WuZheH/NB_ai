import assert from "node:assert/strict";
import test from "node:test";

import {
  COMMIT_OBJECTS_CONFIRMATION_CONTEXT,
  COMMIT_PAPER_CONFIRMATION_CONTEXT,
  buildSuggestionUploadPayload,
  createReviewApi,
} from "../src/features/importing/review/reviewApi.js";
import {
  buildReviewedObjectPayload,
  buildReviewedPackage,
  buildReviewItems,
  createEmptyEvidenceRef,
  normalizeReviewedObject,
  normalizeSuggestedObject,
  normalizeTagValue,
} from "../src/features/importing/review/reviewModel.js";
import {
  addEvidenceRef,
  addReviewTag,
  createCommitPhase,
  editEvidenceField,
  editReviewTag,
  removeEvidenceRef,
  removeReviewTag,
  selectEvidenceSection,
  toggleReviewStatus,
  updateReviewComment,
} from "../src/features/importing/review/reviewState.js";
import {
  continueCommitAfterRemap,
  runCommitPipeline,
} from "../src/features/importing/review/commitPipeline.js";

function sampleObject(overrides = {}) {
  return {
    object_key: "object-1",
    object_name: "Object One",
    object_type: "method",
    aliases: ["One"],
    topic_tags: ["topic"],
    problem_tags: ["problem"],
    mechanism_tags: ["mechanism"],
    inspiration_tags: ["idea"],
    evidence_refs: [{ pdf_page: "7", section_id: "s1" }],
    warnings: ["check"],
    ...overrides,
  };
}

function reviewState(overrides = {}) {
  return {
    reviewItems: [normalizeSuggestedObject(sampleObject())],
    sourceTraceSections: [{ section_id: "s2", title: "Methods", pdf_page: 12 }],
    commitLoading: false,
    commitPhase: createCommitPhase(),
    confirmRemapFailed: false,
    saveStatus: "",
    saveResult: null,
    remapPreview: null,
    ...overrides,
  };
}

function stateHarness(initial = reviewState()) {
  let current = initial;
  return {
    setState(update) {
      current = typeof update === "function" ? update(current) : update;
    },
    get state() {
      return current;
    },
  };
}

test("suggestion and reviewed normalization preserve their distinct contracts", () => {
  const suggestion = normalizeSuggestedObject(sampleObject({
    status: "accepted",
    user_comment: "kept",
  }));
  assert.equal(suggestion.reviewStatus, "accepted");
  assert.equal(suggestion.userComment, "kept");
  assert.deepEqual(suggestion.editedTags.topic_tags, ["topic"]);
  assert.notEqual(suggestion.editedTags.topic_tags, suggestion.topic_tags);
  assert.notEqual(suggestion.editedEvidenceRefs[0], suggestion.evidence_refs[0]);

  const uploaded = normalizeSuggestedObject(sampleObject({
    status: "accepted",
    user_comment: "discarded",
  }), { fromUpload: true });
  assert.equal(uploaded.reviewStatus, "suggested");
  assert.equal(uploaded.userComment, "");

  const reviewed = normalizeReviewedObject(sampleObject({
    review_status: "edited",
    topic_tags: [{ tag: "normalized", status: "accepted" }, "plain"],
    user_comment: "human",
  }));
  assert.equal(reviewed.reviewStatus, "edited");
  assert.deepEqual(reviewed.editedTags.topic_tags, ["normalized", "plain"]);
  assert.equal(reviewed.userComment, "human");
  assert.equal(normalizeTagValue({ tag: "value" }), "value");
  assert.equal(normalizeTagValue({}), "");

  assert.equal(buildReviewItems([sampleObject({ status: "accepted" })], "uploaded_suggestions")[0].reviewStatus, "suggested");
  assert.equal(buildReviewItems([sampleObject({ review_status: "rejected" })], "reviewed_objects")[0].reviewStatus, "rejected");
});

test("reviewed payload keeps exact fields, tag statuses, evidence, and defaults", () => {
  const accepted = normalizeSuggestedObject(sampleObject({ warnings: undefined }));
  accepted.reviewStatus = "accepted";
  accepted.userComment = "reviewed";
  accepted.editedTags.topic_tags.push("");
  const payload = buildReviewedObjectPayload(accepted);

  assert.deepEqual(Object.keys(payload), [
    "object_key",
    "object_name",
    "object_type",
    "review_status",
    "aliases",
    "topic_tags",
    "problem_tags",
    "mechanism_tags",
    "inspiration_tags",
    "evidence_refs",
    "user_comment",
    "warnings",
  ]);
  assert.deepEqual(payload.topic_tags, [
    { tag: "topic", status: "accepted" },
    { tag: "", status: "accepted" },
  ]);
  assert.deepEqual(payload.inspiration_tags, [{ tag: "idea", status: "suggested" }]);
  assert.deepEqual(payload.evidence_refs, [{ pdf_page: "7", section_id: "s1" }]);
  assert.equal(payload.user_comment, "reviewed");
  assert.deepEqual(payload.warnings, []);

  assert.deepEqual(buildReviewedPackage([accepted]), {
    schema_version: "reviewed_object_tag_package_v1",
    reviewed_by: "user",
    objects: [payload],
  });
});

test("review state transitions are immutable and retain evidence semantics", () => {
  const initial = reviewState();
  const status = toggleReviewStatus(initial, 0, "edited");
  const editedTag = editReviewTag(status, 0, "topic_tags", 0, "new topic");
  const addedTag = addReviewTag(editedTag, 0, "topic_tags");
  const removedTag = removeReviewTag(addedTag, 0, "topic_tags", 1);
  const commented = updateReviewComment(removedTag, 0, "comment");
  const addedEvidence = addEvidenceRef(commented, 0);
  const editedEvidence = editEvidenceField(addedEvidence, 0, 1, "quote_text_short", "quote");
  const selected = selectEvidenceSection(editedEvidence, 0, 1, "s2");
  const removedEvidence = removeEvidenceRef(selected, 0, 0);

  assert.equal(initial.reviewItems[0].reviewStatus, "suggested");
  assert.equal(removedEvidence.reviewItems[0].reviewStatus, "edited");
  assert.deepEqual(removedEvidence.reviewItems[0].editedTags.topic_tags, ["new topic"]);
  assert.equal(removedEvidence.reviewItems[0].userComment, "comment");
  assert.deepEqual(removedEvidence.reviewItems[0].editedEvidenceRefs, [{
    ...createEmptyEvidenceRef(),
    quote_text_short: "quote",
    section_id: "s2",
    section_title: "Methods",
    pdf_page: "12",
  }]);
});

test("review API preserves every URL, method adapter, and write payload", async () => {
  const calls = [];
  const api = createReviewApi({
    get(path) {
      calls.push({ method: "GET", path });
      return Promise.resolve({});
    },
    post(path, body) {
      calls.push({ method: "POST", path, body });
      return Promise.resolve({});
    },
  });

  const upload = buildSuggestionUploadPayload({ objects: [{ id: 1 }] });
  assert.deepEqual(upload, {
    schema_version: "object_tag_suggestions_v1",
    created_by: "external_chatgpt_user_pasted",
    objects: [{ id: 1 }],
  });
  await api.fetchSourceTraceSections("job-1");
  await api.uploadSuggestions("job-1", upload);
  await api.fetchSuggestions("job-1");
  await api.fetchReviewedObjects("job-1");
  await api.saveReviewedObjects("job-1", { objects: [] });
  await api.previewReviewedObjectRemap("job-1");
  await api.commitPaper("job-1");
  await api.commitReviewedObjects("job-1");

  assert.deepEqual(calls, [
    { method: "GET", path: "/api/v1/imports/job-1/source-trace-sections" },
    { method: "POST", path: "/api/v1/imports/job-1/ai-suggestions", body: upload },
    { method: "GET", path: "/api/v1/imports/job-1/ai-suggestions" },
    { method: "GET", path: "/api/v1/imports/job-1/reviewed-objects" },
    { method: "POST", path: "/api/v1/imports/job-1/reviewed-objects", body: { objects: [] } },
    { method: "POST", path: "/api/v1/imports/job-1/remap-reviewed-objects-preview", body: {} },
    {
      method: "POST",
      path: "/api/v1/imports/job-1/commit-paper",
      body: { confirm_write: true, confirmation_context: COMMIT_PAPER_CONFIRMATION_CONTEXT },
    },
    {
      method: "POST",
      path: "/api/v1/imports/job-1/commit-reviewed-objects",
      body: { confirm_write: true, confirmation_context: COMMIT_OBJECTS_CONFIRMATION_CONTEXT },
    },
  ]);
});

test("commit pipeline preserves paper, remap, objects, refresh order and already_committed", async () => {
  const calls = [];
  const safety = [];
  let refreshCount = 0;
  const api = {
    async commitPaper(jobId) {
      calls.push(["paper", jobId]);
      return { status: "committed", document_id: 9 };
    },
    async previewReviewedObjectRemap(jobId) {
      calls.push(["remap", jobId]);
      return { status: "ok", summary: { failed: 0 } };
    },
    async commitReviewedObjects(jobId) {
      calls.push(["objects", jobId]);
      return { status: "already_committed" };
    },
  };
  const harness = stateHarness();
  const result = await runCommitPipeline({
    jobId: " job-9 ",
    api,
    setState: harness.setState,
    updateSafety: value => safety.push(value.status),
    onRefresh: () => { refreshCount += 1; },
  });

  assert.deepEqual(calls, [["paper", "job-9"], ["remap", "job-9"], ["objects", "job-9"]]);
  assert.deepEqual(safety, ["committed", "ok", "already_committed"]);
  assert.equal(result.status, "already_committed");
  assert.equal(harness.state.commitPhase.paper.status, "ok");
  assert.equal(harness.state.commitPhase.remap.status, "ok");
  assert.equal(harness.state.commitPhase.objects.status, "already_committed");
  assert.equal(harness.state.saveStatus, "committed");
  assert.equal(refreshCount, 1);
});

test("failed remap pauses before object commit and resumes only after confirmation", async () => {
  const calls = [];
  let refreshCount = 0;
  const api = {
    async commitPaper() {
      calls.push("paper");
      return { status: "already_committed" };
    },
    async previewReviewedObjectRemap() {
      calls.push("remap");
      return { status: "ok", summary: { mapped: 2, failed: 1 } };
    },
    async commitReviewedObjects() {
      calls.push("objects");
      return { status: "ok" };
    },
  };
  const harness = stateHarness();
  const paused = await runCommitPipeline({
    jobId: "job-warning",
    api,
    setState: harness.setState,
  });

  assert.equal(paused.status, "warning");
  assert.deepEqual(calls, ["paper", "remap"]);
  assert.equal(harness.state.commitPhase.paper.status, "already_committed");
  assert.equal(harness.state.commitPhase.remap.status, "warning");
  assert.equal(harness.state.confirmRemapFailed, true);
  assert.equal(harness.state.commitLoading, false);

  await continueCommitAfterRemap({
    jobId: "job-warning",
    api,
    setState: harness.setState,
    onRefresh: () => { refreshCount += 1; },
  });
  assert.deepEqual(calls, ["paper", "remap", "objects"]);
  assert.equal(harness.state.confirmRemapFailed, false);
  assert.equal(harness.state.commitPhase.objects.status, "ok");
  assert.equal(refreshCount, 1);
});

test("pipeline stops on an invalid phase result and never refreshes", async () => {
  const calls = [];
  let refreshed = false;
  const harness = stateHarness();
  const result = await runCommitPipeline({
    jobId: "job-error",
    api: {
      async commitPaper() {
        calls.push("paper");
        return { status: "blocked" };
      },
      async previewReviewedObjectRemap() {
        calls.push("remap");
      },
      async commitReviewedObjects() {
        calls.push("objects");
      },
    },
    setState: harness.setState,
    onRefresh: () => { refreshed = true; },
  });
  assert.deepEqual(calls, ["paper"]);
  assert.equal(result.phase, "paper");
  assert.equal(harness.state.commitPhase.paper.status, "error");
  assert.equal(refreshed, false);
});

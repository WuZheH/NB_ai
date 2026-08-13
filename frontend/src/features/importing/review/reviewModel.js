import { REVIEWED_OBJECT_SCHEMA_VERSION } from "./reviewApi.js";

export const REVIEW_TAG_LAYERS = [
  "topic_tags",
  "problem_tags",
  "mechanism_tags",
  "inspiration_tags",
];

export function normalizeTagValue(value) {
  return typeof value === "string" ? value : (value?.tag || "");
}

export function createEmptyEvidenceRef() {
  return {
    pdf_page: "",
    section_id: "",
    section_title: "",
    quote_text_short: "",
    paper_md_anchor: "",
  };
}

function copyEvidenceRefs(object = {}) {
  return (object.evidence_refs || []).map(ref => ({ ...ref }));
}

function suggestedTags(object = {}) {
  return Object.fromEntries(
    REVIEW_TAG_LAYERS.map(layer => [layer, [...(object[layer] || [])]]),
  );
}

function reviewedTags(object = {}) {
  return Object.fromEntries(
    REVIEW_TAG_LAYERS.map(layer => [
      layer,
      (object[layer] || []).map(normalizeTagValue),
    ]),
  );
}

export function normalizeSuggestedObject(object = {}, { fromUpload = false } = {}) {
  return {
    ...object,
    reviewStatus: fromUpload ? "suggested" : (object.status || "suggested"),
    editedTags: suggestedTags(object),
    editedEvidenceRefs: copyEvidenceRefs(object),
    userComment: fromUpload ? "" : (object.user_comment || ""),
  };
}

export function normalizeReviewedObject(object = {}) {
  return {
    ...object,
    reviewStatus: object.review_status || "suggested",
    editedTags: reviewedTags(object),
    editedEvidenceRefs: copyEvidenceRefs(object),
    userComment: object.user_comment || "",
  };
}

export function buildReviewItems(objects = [], source = "ai_suggestions") {
  if (source === "reviewed_objects") {
    return objects.map(normalizeReviewedObject);
  }
  const fromUpload = source === "uploaded_suggestions";
  return objects.map(object => normalizeSuggestedObject(object, { fromUpload }));
}

function payloadTags(item, layer) {
  const status = layer === "inspiration_tags"
    ? "suggested"
    : (item.reviewStatus === "accepted" ? "accepted" : "suggested");
  return (item.editedTags?.[layer] || []).map(tag => ({ tag, status }));
}

export function buildReviewedObjectPayload(item = {}) {
  return {
    object_key: item.object_key,
    object_name: item.object_name,
    object_type: item.object_type,
    review_status: item.reviewStatus,
    aliases: item.aliases || [],
    topic_tags: payloadTags(item, "topic_tags"),
    problem_tags: payloadTags(item, "problem_tags"),
    mechanism_tags: payloadTags(item, "mechanism_tags"),
    inspiration_tags: payloadTags(item, "inspiration_tags"),
    evidence_refs: item.editedEvidenceRefs || item.evidence_refs || [],
    user_comment: item.userComment || "",
    warnings: item.warnings || [],
  };
}

export function buildReviewedPackage(items = []) {
  return {
    schema_version: REVIEWED_OBJECT_SCHEMA_VERSION,
    reviewed_by: "user",
    objects: items.map(buildReviewedObjectPayload),
  };
}

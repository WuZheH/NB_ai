export const REVIEWED_OBJECT_SCHEMA_VERSION = "reviewed_object_tag_package_v1";
export const COMMIT_PAPER_CONFIRMATION_CONTEXT = "commit_paper_after_preview";
export const COMMIT_OBJECTS_CONFIRMATION_CONTEXT = "commit_reviewed_objects_after_remap";

function jobRoute(jobId, suffix) {
  return `/api/v1/imports/${jobId}/${suffix}`;
}

export function buildSuggestionUploadPayload(parsed = {}) {
  return {
    schema_version: parsed.schema_version || "object_tag_suggestions_v1",
    created_by: parsed.created_by || "external_chatgpt_user_pasted",
    objects: parsed.objects,
  };
}

export function createReviewApi({ get, post }) {
  return {
    fetchSourceTraceSections(jobId) {
      return get(jobRoute(jobId, "source-trace-sections"));
    },
    uploadSuggestions(jobId, payload) {
      return post(jobRoute(jobId, "ai-suggestions"), payload);
    },
    fetchSuggestions(jobId) {
      return get(jobRoute(jobId, "ai-suggestions"));
    },
    fetchReviewedObjects(jobId) {
      return get(jobRoute(jobId, "reviewed-objects"));
    },
    saveReviewedObjects(jobId, payload) {
      return post(jobRoute(jobId, "reviewed-objects"), payload);
    },
    previewReviewedObjectRemap(jobId) {
      return post(jobRoute(jobId, "remap-reviewed-objects-preview"), {});
    },
    commitPaper(jobId) {
      return post(jobRoute(jobId, "commit-paper"), {
        confirm_write: true,
        confirmation_context: COMMIT_PAPER_CONFIRMATION_CONTEXT,
      });
    },
    commitReviewedObjects(jobId) {
      return post(jobRoute(jobId, "commit-reviewed-objects"), {
        confirm_write: true,
        confirmation_context: COMMIT_OBJECTS_CONFIRMATION_CONTEXT,
      });
    },
  };
}

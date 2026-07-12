import { postJson } from "../api/client.js";

export const RETRIEVAL_SEARCH_ENDPOINT = "/api/v1/retrieval/search";
export const RETRIEVAL_SELECTION_ENDPOINT = "/api/v1/retrieval/selection/resolve";
export const RETRIEVAL_EXPORT_ENDPOINT = "/api/v1/retrieval/evidence/export";

export function searchLocalRetrieval(request) {
  return postJson(RETRIEVAL_SEARCH_ENDPOINT, request);
}

export function resolveRetrievalSelection(selector) {
  return postJson(RETRIEVAL_SELECTION_ENDPOINT, selector);
}

export function exportRetrievalEvidence(request) {
  return postJson(RETRIEVAL_EXPORT_ENDPOINT, request);
}

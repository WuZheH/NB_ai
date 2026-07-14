import { getJson, postJson } from "../api/client.js";

export const RETRIEVAL_SEARCH_ENDPOINT = "/api/v1/retrieval/search";
export const NOTEBOOK_SEARCH_ENDPOINT = "/api/v1/retrieval/notebook-search";
export const RETRIEVAL_FRAGMENT_ENDPOINT = "/api/v1/retrieval/fragments";
export const RETRIEVAL_SELECTION_ENDPOINT = "/api/v1/retrieval/selection/resolve";
export const RETRIEVAL_EXPORT_ENDPOINT = "/api/v1/retrieval/evidence/export";

export function searchLocalRetrieval(request) {
  return postJson(RETRIEVAL_SEARCH_ENDPOINT, request);
}

export function searchNotebookRetrieval(request, options = {}) {
  return postJson(NOTEBOOK_SEARCH_ENDPOINT, request, options);
}

export function fetchRetrievalFragment(fragmentId, options = {}) {
  const normalizedId = String(fragmentId || "").trim();
  if (!normalizedId) return Promise.reject(new Error("fragment_id is required"));
  return getJson(`${RETRIEVAL_FRAGMENT_ENDPOINT}/${encodeURIComponent(normalizedId)}`, options);
}

export function fetchRetrievalFragmentLocator(fragmentId, options = {}) {
  const normalizedId = String(fragmentId || "").trim();
  if (!normalizedId) return Promise.reject(new Error("fragment_id is required"));
  return getJson(`${RETRIEVAL_FRAGMENT_ENDPOINT}/${encodeURIComponent(normalizedId)}/locator`, options);
}

export function resolveRetrievalSelection(selector) {
  return postJson(RETRIEVAL_SELECTION_ENDPOINT, selector);
}

export function exportRetrievalEvidence(request) {
  return postJson(RETRIEVAL_EXPORT_ENDPOINT, request);
}

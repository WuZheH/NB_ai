import type { FragmentDetail, SearchResult, SearchViewModel, ToolEnvelope } from "../types";

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asResults(value: unknown): SearchResult[] {
  return Array.isArray(value) ? (value.filter((entry) => entry && typeof entry === "object") as SearchResult[]) : [];
}

export function searchViewModel(envelope: ToolEnvelope | null): SearchViewModel {
  if (!envelope) {
    return { status: "loading", query: "", resultCount: 0, results: [], warnings: [] };
  }
  const structured = asObject(envelope.structuredContent);
  const meta = asObject(envelope._meta);
  const results = asResults(meta["notebookAi/results"] ?? structured.results);
  const warnings = Array.isArray(structured.warnings)
    ? structured.warnings.map(formatWarning)
    : [];
  const error = envelope.isError || structured.status === "error" ? String(structured.message ?? "READ failed.") : undefined;
  return {
    status: String(structured.status ?? (error ? "error" : "ok")),
    query: String(structured.query ?? window.openai?.toolInput?.query ?? ""),
    resultCount: Number(structured.result_count ?? results.length),
    results,
    warnings,
    error,
  };
}

function formatWarning(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "READ returned a warning.";
  }
  const warning = value as Record<string, unknown>;
  const ids = Array.isArray(warning.document_ids)
    ? warning.document_ids.filter((item) => Number.isInteger(item)).join(", ")
    : "";
  if (warning.code === "requested_document_not_found") {
    return ids
      ? `Requested document not found: ${ids}`
      : "A requested document was not found.";
  }
  if (warning.code === "requested_document_archived") {
    return ids
      ? `Requested document is archived: ${ids}`
      : "A requested document is archived.";
  }
  return typeof warning.code === "string"
    ? warning.code.replaceAll("_", " ")
    : "READ returned a warning.";
}

export function fetchedFragment(envelope: ToolEnvelope): FragmentDetail | null {
  const meta = asObject(envelope._meta);
  const structured = asObject(envelope.structuredContent);
  const fragment = meta["notebookAi/fragment"] ?? structured.fragment;
  return fragment && typeof fragment === "object" ? (fragment as FragmentDetail) : null;
}

export function exportedContent(envelope: ToolEnvelope): string {
  const meta = asObject(envelope._meta);
  return typeof meta["notebookAi/exportContent"] === "string" ? meta["notebookAi/exportContent"] : "";
}

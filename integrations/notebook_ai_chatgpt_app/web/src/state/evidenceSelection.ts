import type { SearchResult } from "../types";

export function toggleEvidence(selected: string[], fragmentId: string): string[] {
  return selected.includes(fragmentId) ? selected.filter((id) => id !== fragmentId) : [...selected, fragmentId];
}

export function selectedEvidence(results: SearchResult[], selected: string[]): SearchResult[] {
  const byId = new Map(results.map((result) => [result.fragment_id, result]));
  return selected.map((id) => byId.get(id)).filter((result): result is SearchResult => Boolean(result));
}

export function selectionContext(results: SearchResult[], selected: string[]): string {
  const evidence = selectedEvidence(results, selected);
  if (!evidence.length) {
    return "No NOTEBOOK_AI evidence is currently selected in the widget.";
  }
  const lines = evidence.map(
    (result) =>
      `- ${result.fragment_id} | ${result.source_type} | ${result.document_title ?? "Untitled"}` +
      `${result.page_label ?? result.pdf_page ? ` | page ${result.page_label ?? result.pdf_page}` : ""}`,
  );
  return `The user selected these NOTEBOOK_AI evidence fragments:\n${lines.join("\n")}`;
}

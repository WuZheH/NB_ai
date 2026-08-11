import type { SearchResult } from "../types";

export interface PinnedEvidence {
  fragment_id: string;
  source_type: SearchResult["source_type"];
  document_title: string | null;
  page_label?: string;
  pdf_page?: number;
}

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
    return "No READ evidence is currently selected in the widget.";
  }
  const lines = evidence.map(
    (result) =>
      `- ${result.fragment_id} | ${result.source_type} | ${result.document_title ?? "Untitled"}` +
      `${result.page_label ?? result.pdf_page ? ` | page ${result.page_label ?? result.pdf_page}` : ""}`,
  );
  return `The user selected these READ evidence fragments:\n${lines.join("\n")}`;
}

export function pinnedEvidence(results: SearchResult[], selected: string[]): PinnedEvidence[] {
  return selectedEvidence(results, selected).map((result) => ({
    fragment_id: result.fragment_id,
    source_type: result.source_type,
    document_title: result.document_title,
    ...(result.page_label
      ? { page_label: result.page_label }
      : result.pdf_page != null
        ? { pdf_page: result.pdf_page }
        : {}),
  }));
}

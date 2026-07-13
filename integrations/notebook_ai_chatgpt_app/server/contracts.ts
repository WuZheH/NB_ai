export const NOTEBOOK_SOURCE_TYPES = [
  "pdf_chunk",
  "zotero_annotation_comment",
  "zotero_child_note",
  "zotero_inspiration_note",
] as const;

export type NotebookSourceType = (typeof NOTEBOOK_SOURCE_TYPES)[number];
export type EvidenceFormat = "markdown" | "jsonl" | "json";

export interface NotebookSearchInput {
  query: string;
  limit: number;
  source_types: NotebookSourceType[];
  document_ids: number[];
  include_context: boolean;
}

export interface NotebookResult {
  fragment_id: string;
  source_type: NotebookSourceType;
  final_rank: number | null;
  final_score: number | null;
  reranker_score: number | null;
  semantic_score: number | null;
  document_id: number | null;
  document_title: string | null;
  document_type: string | null;
  chunk_id: number | null;
  pdf_page: number | null;
  page_label: string | null;
  text: string | null;
  selected_text: string | null;
  note_text: string | null;
  context_before: string | null;
  context_after: string | null;
  tags: string[];
  provenance: Array<Record<string, unknown>>;
  open_target: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface NotebookSearchResponse {
  status: string;
  query: string;
  mode: string;
  embedding_model: string;
  reranker_model: string;
  backend: string;
  result_count: number;
  results: NotebookResult[];
  warnings: string[];
  latency: Record<string, unknown> | number | null;
  [key: string]: unknown;
}

export interface FragmentResponse {
  status?: string;
  fragment?: NotebookResult;
  result?: NotebookResult;
  [key: string]: unknown;
}

export interface EvidenceExportInput {
  fragment_ids: string[];
  format: EvidenceFormat;
  query?: string;
}

export interface EvidenceExportResponse {
  status?: string;
  format?: EvidenceFormat;
  item_count?: number;
  fragment_count?: number;
  content?: string;
  text?: string;
  output?: string;
  [key: string]: unknown;
}

export function unwrapFragment(payload: FragmentResponse | NotebookResult): NotebookResult {
  if ("fragment" in payload && payload.fragment) {
    return payload.fragment;
  }
  if ("result" in payload && payload.result) {
    return payload.result;
  }
  return payload as NotebookResult;
}

export function exportContent(payload: EvidenceExportResponse | string): string {
  if (typeof payload === "string") {
    return payload;
  }
  return payload.content ?? payload.text ?? payload.output ?? JSON.stringify(payload, null, 2);
}

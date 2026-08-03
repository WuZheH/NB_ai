export type SourceType =
  | "pdf_chunk"
  | "zotero_annotation_comment"
  | "zotero_child_note"
  | "zotero_inspiration_note";

export type EvidenceFormat = "markdown" | "jsonl" | "json";

export interface SearchResult {
  fragment_id: string;
  source_type: SourceType;
  selection_rank: number | null;
  document_id: number | null;
  document_title: string | null;
  document_type: string | null;
  pdf_page: number | null;
  page_label: string | null;
  heading: string | null;
  section: string | null;
  coherent_text: string | null;
  selected_source_text: string | null;
  user_note: string | null;
  context_before: string | null;
  context_after: string | null;
  tags: string[];
  provenance: Record<string, unknown>;
  open_target: Record<string, unknown> | null;
  [key: string]: unknown;
}

export type FragmentDetail = SearchResult;

export interface ToolEnvelope {
  content?: Array<{ type: string; text?: string }>;
  structuredContent?: Record<string, unknown>;
  _meta?: Record<string, unknown>;
  isError?: boolean;
}

export interface SearchViewModel {
  status: string;
  query: string;
  resultCount: number;
  results: SearchResult[];
  warnings: string[];
  error?: string;
}

declare global {
  interface Window {
    openai?: {
      toolInput?: Record<string, unknown>;
      toolOutput?: Record<string, unknown>;
      toolResponseMetadata?: Record<string, unknown>;
      widgetState?: Record<string, unknown>;
      callTool?: (name: string, args: Record<string, unknown>) => Promise<ToolEnvelope>;
      setWidgetState?: (state: Record<string, unknown>) => Promise<void> | void;
      sendFollowUpMessage?: (options: { prompt: string; scrollToBottom?: boolean }) => Promise<void> | void;
      openExternal?: (options: { href: string }) => Promise<void> | void;
    };
  }
}

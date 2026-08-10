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

export interface NotebookFragment {
  fragment_id: string;
  source_type: NotebookSourceType;
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
  selection_rank: number | null;
  [key: string]: unknown;
}

export type NotebookResult = NotebookFragment;

export interface NotebookSearchResponse {
  status: string;
  query: string;
  mode: string;
  embedding_model: string;
  reranker_model: string;
  backend: string;
  result_count: number;
  results: NotebookResult[];
  warnings: Array<string | Record<string, unknown>>;
  latency: Record<string, unknown> | number | null;
  [key: string]: unknown;
}

export interface FragmentResponse {
  status?: string;
  fragment?: NotebookFragment;
  result?: NotebookFragment;
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

export interface ImportedLibraryItem {
  document_id: number;
  title: string;
  type: string;
  imported_at: string;
  chunk_count: number;
  has_pdf: boolean;
  duplicate_status: string;
  status: "active" | "archived";
  source: "search_library";
}
export interface CatalogLibraryItem {
  kind: "catalog";
  document_id: null;
  title: string;
  type: "pdf";
  has_pdf: true;
  import_ref: string;
  file_name: string;
  relative_path: string;
  note_count: number;
  note_files: string[];
  status: "available";
  duplicate_status: string;
  source: "search_import_catalog";
}
export interface ZoteroLibraryItem {
  kind: "zotero";
  document_id: number | null;
  title: string;
  item_type: string;
  zotero_item_key: string;
  parent_key: string;
  authors: string[];
  tags?: string[];
  date: string;
  attachment_keys: string[];
  primary_pdf_attachment_key: string | null;
  attachment_selection_required: boolean;
  has_pdf: boolean;
  pdf_attachment_count: number;
  attachment_count: number;
  attachment_choices: Array<{ zotero_attachment_key: string; file_name: string | null; path_exists: boolean; content_type: string | null }>;
  annotation_count: number;
  child_note_count: number;
  duplicate_status: string;
  status: "available" | "imported";
  source: "zotero_library";
}
export type LibraryItem = ImportedLibraryItem | CatalogLibraryItem | ZoteroLibraryItem;

export interface ListLibraryInput {
  scope: "imported" | "catalog" | "zotero";
  query?: string;
  document_type?: string;
  status: "active" | "archived" | "available" | "imported" | "all";
  limit: number;
}

export interface ListLibraryResponse {
  status: "ok";
  count: number;
  total_matches?: number;
  items: LibraryItem[];
  truncated: boolean;
  scope: "imported" | "catalog" | "zotero";
  warnings?: Array<Record<string, unknown>>;
  applied_filters?: Record<string, unknown>;
}

export interface IntegrityReportInput {
  document_id: number;
}

export interface IntegrityReportResponse {
  status: "ok";
  read_only: true;
  verdict: "pass" | "warn" | "fail";
  warnings: string[];
  document_id: number;
  pdf_sha256: string;
  document: Record<string, unknown>;
  source: Record<string, unknown>;
  database: {
    document_count: number;
    chunk_count: number;
    chapter_count: number;
    source_binding_count: number;
    personal_note_count: number;
    evidence_link_count: number;
    integrity_check: string;
    foreign_key_issue_count: number;
  };
  fts: {
    status: string;
    ready: boolean;
    expected_pdf_chunk_count: number;
    indexed_pdf_chunk_count: number;
    missing_pdf_chunk_count: number;
    orphan_pdf_chunk_count: number;
    eligible_personal_note_count: number;
    indexed_personal_note_count: number;
    missing_personal_note_count: number;
    orphan_personal_note_count: number;
    excluded_personal_note_count: number;
    exclusion_reasons: Record<string, number>;
    fragment_count: number;
    source_type_counts: Record<string, number>;
    reasons: string[];
  };
  vectors: {
    status: string;
    passage_expected_count: number;
    passage_indexed_count: number;
    passage_missing_count: number;
    passage_orphan_count: number | "not_available";
    note_expected_count: number;
    note_indexed_count: number;
    note_missing_count: number;
    note_orphan_count: number | "not_available";
    reasons: string[];
  };
  history: {
    confirmation_token_fingerprint: string;
    previewed_at: string;
    confirmed_at: string;
    transaction_fingerprint: string;
    source_revision_fingerprint: string;
    lifecycle_events: string;
    terminal_status: string;
    terminal_stage: string;
    journal_operation_id: string;
    journal_revision: number | "not_recorded";
    receipt_recorded: boolean | "not_recorded";
    journal_updated_at: string;
    journal_terminal_events: string;
  };
  writes_performed: {
    production_db: false;
    fts: false;
    vector_store: false;
    zotero: false;
  };
}

export interface ImportPreviewInput {
  source_type?: "local_pdf" | "zotero_selected_book";
  inbox_filename?: string;
  zotero_item_key?: string;
  zotero_attachment_key?: string;
}

export interface OpenAIFileInput {
  download_url: string;
  file_id: string;
  mime_type?: string;
  file_name?: string;
}

export interface ImportPreviewResponse {
  status: "ok";
  operation_id: string | null;
  source_type: "local_pdf" | "zotero_selected_book";
  filename: string | null;
  title: string;
  item_type: string | null;
  parent_key?: string | null;
  zotero_item_key?: string | null;
  zotero_attachment_key?: string | null;
  pdf_sha256: string | null;
  duplicate_status: string;
  existing_document_id: number | null;
  estimated_pages: number | null;
  estimated_chunks: number | null;
  extractor_strategy?: "native_text" | "high_quality_pdf_to_markdown" | null;
  text_quality_score?: number | null;
  quality_reasons?: string[];
  converted_markdown_status?: string | null;
  converted_markdown_path?: string | null;
  extraction_ready?: boolean | null;
  blockers?: Array<Record<string, unknown>>;
  document_type: string;
  warnings: string[];
  confirmation_token: string | null;
  confirmation_expires_in_seconds: number | null;
  attachment_choices: Array<{
    zotero_attachment_key: string;
    file_name: string | null;
    path_exists: boolean;
    path_status: string | null;
    content_type: string | null;
    date_modified: string | null;
    version: number | string | null;
  }>;
  annotation_count: number | null;
  child_note_count: number | null;
  note_count?: number;
  note_files?: string[];
}

export interface ImportDocumentInput {
  confirmation_token: string;
  confirmed: true;
}

export interface ImportDocumentResponse {
  status: string;
  tool?: string;
  operation_id?: string | null;
  terminal?: boolean | null;
  document_id?: number | null;
  title?: string;
  document_type?: string;
  chunk_count?: number;
  duplicate_status?: string;
  error_code?: string | null;
  message?: string;
  retryable?: boolean | null;
  already_completed?: boolean;
  replayed_receipt?: boolean | null;
  operation_in_progress?: boolean | null;
  token_consumed?: boolean | null;
  writes_performed: boolean | null;
  safe_to_retry?: boolean | null;
  publish_substage?: string | null;
  cause_type?: string | null;
  cause_message?: string | null;
  cause_errno?: number | null;
  cause_winerror?: number | null;
  cause_filename?: string | null;
  cause_filename2?: string | null;
  rollback_attempted?: boolean | null;
  rollback_completed?: boolean | null;
  error_stage?: string | null;
}

export interface ImportStatusInput {
  operation_id: string;
}

export type ImportOperationStatus =
  | "accepted"
  | "running"
  | "committed"
  | "failed"
  | "orphaned";

export interface ImportStatusResponse {
  status: ImportOperationStatus;
  operation_id: string;
  document_id: number | null;
  title: string | null;
  document_type: string | null;
  chunk_count: number | null;
  terminal: boolean;
  operation_in_progress: boolean;
  writes_performed: boolean | null;
  token_consumed: boolean | null;
  safe_to_retry: boolean;
  replayed_receipt: boolean;
  error_code: string | null;
  error_stage: string | null;
  rollback_attempted: boolean | null;
  rollback_completed: boolean | null;
}

export interface DeletePreviewResponse {
  status: "ok";
  document_id: number;
  title: string;
  safe_to_delete: boolean;
  pdf_preserved: boolean;
  notes_preserved: boolean;
  blockers: string[];
  confirmation_token: string;
  confirmation_expires_in_seconds: number;
}

export interface DeleteDocumentInput {
  confirmation_token: string;
  confirmed: true;
}

export interface DeleteDocumentResponse {
  status: string;
  document_id: number;
  title: string;
  recovery_created: boolean;
  cleanup_complete: boolean;
  error_code: string | null;
}

export function unwrapFragment(payload: FragmentResponse | NotebookFragment): NotebookFragment {
  if ("fragment" in payload && payload.fragment) {
    return payload.fragment;
  }
  if ("result" in payload && payload.result) {
    return payload.result;
  }
  return payload as NotebookFragment;
}

export function exportContent(payload: EvidenceExportResponse | string): string {
  if (typeof payload === "string") {
    return payload;
  }
  return payload.content ?? payload.text ?? payload.output ?? JSON.stringify(payload, null, 2);
}

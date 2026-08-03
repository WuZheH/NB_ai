import { useState } from "react";
import { buildTrace, sourceFields, EMPTY_SAFETY } from "../utils/formatters.js";

export function useSelectionInspector() {
  const [selectedEvidenceId, setSelectedEvidenceId] = useState(null);
  const [sourceTrace, setSourceTrace] = useState(null);
  const [safety, setSafety] = useState(EMPTY_SAFETY);

  function updateSafety(payload) {
    const dbWrite = payload.db_write_performed ?? payload.core_db_write_performed;
    setSafety({
      production_write_enabled: Boolean(payload.production_write_enabled),
      external_llm_called: Boolean(payload.external_llm_called),
      db_write_performed: Boolean(dbWrite),
      mechanism_generated: Boolean(payload.mechanism_generated || payload.final_hypothesis_created)
    });
  }

  function clearSelection() {
    setSelectedEvidenceId(null);
    setSourceTrace({ selection_type: "none" });
  }

  function selectDocument(document) {
    if (!document) {
      clearSelection();
      return;
    }
    setSourceTrace({
      selection_type: "document",
      document_id: document.document_id,
      title: document.title,
      pdf_page: document.pdf_page,
      ...sourceFields(document)
    });
  }

  function selectEvidence(evidence, fallback = {}) {
    if (!evidence && !fallback) {
      clearSelection();
      return;
    }
    const item = { ...(fallback || {}), ...(evidence || {}) };
    setSelectedEvidenceId(item.chunk_id || null);
    const traceSource = { ...item, ...(item.source_trace || {}) };
    setSourceTrace(
      buildTrace(traceSource, {
        selection_type: "evidence",
        title: item.title || item.document_title,
        document_id: item.document_id,
        chunk_id: item.chunk_id,
        pdf_page: item.pdf_page_start ?? item.pdf_page,
        search_query: item.search_query,
        fallback_terms: item.fallback_terms,
        snippet: item.snippet,
        locator_status: item.locator_status,
        locator_reason: item.locator_reason,
        is_locatable: item.is_locatable,
        is_metadata_chunk: item.is_metadata_chunk,
        highlight_count: item.highlight_count,
      })
    );
  }

  function selectObject(object) {
    if (!object) {
      clearSelection();
      return;
    }
    setSourceTrace({
      selection_type: "object",
      object_key: object.object_key,
      title: object.object_name,
      document_id: object.document_id,
      chunk_id: object.evidence_refs?.[0]?.chunk_id,
      pdf_page: object.evidence_refs?.[0]?.pdf_page,
      locator_status: object.evidence_refs?.[0]?.locator_status,
      locator_reason: object.evidence_refs?.[0]?.locator_reason,
    });
  }

  function selectZoteroSource(source) {
    if (!source) return;
    const importStatus = source.import_status || (source.already_imported || source.imported ? "exact_imported" : "not_imported");
    const recommendedAction = source.recommended_action || ZOTERO_IMPORT_STATUS_RECOMMENDED_ACTION[importStatus] || "select_for_import";
    setSourceTrace({
      selection_type: "zotero_source",
      title: source.title,
      zotero_pdf_source_id: source.id,
      zotero_item_key: source.zotero_item_key || source.item_key || source.parent_item_key,
      zotero_attachment_key: source.zotero_attachment_key || source.attachment_key,
      resolved_pdf_path: source.resolved_pdf_path || source.pdf_path || source.path || source.attachment_path,
      path_exists: source.path_exists,
      cache_status: source.cache_status,
      import_status: importStatus,
      existing_document_id: source.existing_document_id || source.primary_document_id || source.linked_document_id,
      existing_document_title: source.existing_document_title || source.existing_documents?.[0]?.title,
      recommended_action: recommendedAction,
      match_reason: source.match_reason,
      matching_reasons: source.matching_reasons || source.existing_documents?.[0]?.matched_by,
    });
  }

  function selectImportJob(result) {
    if (!result?.import_job_id) return;
    setSourceTrace({
      selection_type: "import_job",
      title: result.import_job_id,
      import_job_id: result.import_job_id,
      paper_md_path: result.paper_md_path,
      source_trace_path: result.source_trace_path,
    });
  }

  function importPreviewSelection(state) {
    if (state.previewResult?.import_job_id) {
      return {
        selection_type: "import_job",
        title: state.previewResult.import_job_id,
        import_job_id: state.previewResult.import_job_id,
        paper_md_path: state.previewResult.paper_md_path,
        source_trace_path: state.previewResult.source_trace_path,
      };
    }
    return { selection_type: "none" };
  }

  return {
    selectedEvidenceId,
    sourceTrace,
    safety,
    setSourceTrace,
    updateSafety,
    clearSelection,
    selectDocument,
    selectEvidence,
    selectObject,
    selectZoteroSource,
    selectImportJob,
    importPreviewSelection,
  };
}

const ZOTERO_IMPORT_STATUS_RECOMMENDED_ACTION = {
  exact_imported: "open_existing_document",
  sibling_imported: "view_existing_document",
  path_imported: "open_existing_document",
  fingerprint_imported: "open_existing_document",
  not_imported: "select_for_import",
  unknown: "recheck_import_status",
};

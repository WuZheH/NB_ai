import { useState } from "react";
import { getJson } from "../api/client.js";
import {
  normalizeReadShelfPayload,
  presentReadShelfError,
} from "../features/library/utils/readShelfContract.js";
import { selectTrustedZoteroCandidate, zoteroCandidateMessage } from "../utils/formatters.js";

const EMPTY_SIDECARS = {
  embeddingSidecar: { status: "idle", results: [], error: "", model: "", timing: null },
  rerankerSidecar: { status: "idle", results: [], error: "", embeddingModel: "", rerankerModel: "", timing: null },
  semanticObjects: { status: "idle", results: [], error: "" },
};

export function useLibraryData({
  updateSafety,
  clearSelection,
  selectDocument,
  selectEvidence,
  selectObject,
  setSourceTrace,
  setView,
  setReturnView,
  view,
}) {
  const [readShelf, setReadShelf] = useState({
    status: "idle",
    items: [],
    error: "",
    errorCode: "",
    errorTitle: "",
  });
  const [searchState, setSearchState] = useState({
    query: "",
    status: "idle",
    results: [],
    objects: [],
    papers: [],
    grouped: false,
    resultMode: "",
    fallbackNotice: "",
    ...EMPTY_SIDECARS,
    error: ""
  });
  const [documentState, setDocumentState] = useState({ status: "idle", data: null, error: "" });
  const [evidenceState, setEvidenceState] = useState({ status: "idle", data: null, error: "" });
  const [evidenceObjectState, setEvidenceObjectState] = useState({ byChunkId: {} });
  const [objectDetailState, setObjectDetailState] = useState({ status: "idle", data: null, error: "" });
  const [zoteroCandidateState, setZoteroCandidateState] = useState({ byDocumentId: {} });
  const [selectedDocumentId, setSelectedDocumentId] = useState(null);

  async function loadReadShelf() {
    setReadShelf({ status: "loading", items: [], error: "", errorCode: "", errorTitle: "" });
    try {
      const payload = await getJson("/api/v1/library/read-shelf");
      const normalized = normalizeReadShelfPayload(payload);
      setReadShelf({
        status: normalized.status,
        items: normalized.items,
        error: normalized.message,
        errorCode: "",
        errorTitle: "",
      });
      updateSafety(payload);
    } catch (error) {
      const presentation = presentReadShelfError(error);
      setReadShelf({
        status: "error",
        items: [],
        error: presentation.message,
        errorCode: presentation.code,
        errorTitle: presentation.title,
      });
    }
  }

  async function runSearch(event) {
    event.preventDefault();
    const query = searchState.query.trim();
    if (!query) {
      setSearchState((state) => ({
        ...state,
        status: "empty",
        results: [],
        objects: [],
        papers: [],
        grouped: false,
        resultMode: "",
        fallbackNotice: "",
        ...EMPTY_SIDECARS,
        error: ""
      }));
      clearSelection();
      return;
    }
    clearSelection();
    setSearchState((state) => ({
      ...state,
      status: "loading",
      results: [],
      objects: [],
      papers: [],
      grouped: true,
      resultMode: "high_quality_search_v1",
      fallbackNotice: "",
      ...EMPTY_SIDECARS,
      error: ""
    }));
    try {
      const payload = await getJson(
        `/api/v1/library/search/high-quality?q=${encodeURIComponent(query)}&object_limit=50&passage_recall_limit=30&passage_limit=15`
      );
      const objects = payload.objects || [];
      const papers = payload.papers || [];
      setSearchState((state) => ({
        ...state,
        status: papers.length || objects.length ? "ready" : "empty",
        results: papers,
        objects,
        papers,
        grouped: true,
        resultMode: payload.mode,
        retrievalBackend: payload.retrieval_backend,
        fallbackReason: payload.fallback_reason,
        vectorStoreStatus: payload.vector_store_status,
        degradedReason: payload.degraded_reason,
        debug: payload.debug || null,
        fallbackNotice: "",
        error: ""
      }));
      if (!papers.length && !objects.length) {
        clearSelection();
      }
      updateSafety(payload);
    } catch (error) {
      await runBasicSearchFallback(query);
    }
  }

  async function runBasicSearchFallback(query) {
    try {
      const payload = await getJson(
        `/api/v1/library/search?q=${encodeURIComponent(query)}&group_by=document&mode=hybrid&limit_documents=5&limit_chunks_per_document=5`
      );
      const objects = payload.objects || [];
      const results = payload.results || [];
      setSearchState((state) => ({
        ...state,
        status: results.length || objects.length ? "ready" : "empty",
        results,
        objects,
        papers: [],
        grouped: Boolean(payload.grouped),
        resultMode: payload.mode,
        fallbackNotice: "高质量搜索暂不可用，已使用基础搜索结果。",
        ...EMPTY_SIDECARS,
        error: ""
      }));
      updateSafety(payload);
    } catch (fallbackError) {
      setSearchState((state) => ({
        ...state,
        status: "error",
        results: [],
        objects: [],
        papers: [],
        ...EMPTY_SIDECARS,
        fallbackNotice: "",
        error: "搜索失败：本地 API 暂不可用。"
      }));
    }
  }

  async function openDocument(documentId) {
    if (!documentId) return;
    setView("document");
    setReturnView("readShelf");
    setSelectedDocumentId(documentId);
    setDocumentState({ status: "loading", data: null, error: "" });
    setSourceTrace({ selection_type: "document", document_id: documentId });
    try {
      const payload = await getJson(`/api/v1/library/documents/${documentId}`);
      const payloadWithInspiration = await attachInspirationNotesPreview(documentId, payload);
      if (isBookLikeChapteredDocument(payloadWithInspiration.document)) {
        const bookPayload = await getJson(`/api/v1/library/books/${documentId}`);
        const bookDocument = {
          document_id: bookPayload.document_id,
          title: bookPayload.title,
          document_type: bookPayload.document_type,
          object_import_mode: bookPayload.object_import_mode,
          object_import_status: bookPayload.object_import_status,
        };
        setDocumentState({
          status: "ready",
          data: {
            ...payloadWithInspiration,
            document: { ...payloadWithInspiration.document, ...bookDocument },
            book_detail: bookPayload,
            is_book_detail: true,
          },
          error: ""
        });
        updateSafety(bookPayload);
        selectDocument({ ...payloadWithInspiration.document, ...bookDocument });
        return;
      }
      setDocumentState({ status: "ready", data: payloadWithInspiration, error: "" });
      updateSafety(payload);
      loadZoteroCandidate(documentId);
      selectDocument(payloadWithInspiration.document);
    } catch (error) {
      setDocumentState({
        status: "error",
        data: null,
        error: "文档不存在或本地 API 暂不可用。"
      });
    }
  }

  async function attachInspirationNotesPreview(documentId, payload) {
    try {
      const inspirationPayload = await getJson(`/api/v1/zotero/inspiration-notes/by-document/${documentId}`);
      return {
        ...payload,
        inspiration_notes_preview: inspirationPayload.items || [],
        inspiration_notes_count: inspirationPayload.count || 0,
        inspiration_notes_error: ""
      };
    } catch (error) {
      return {
        ...payload,
        inspiration_notes_preview: [],
        inspiration_notes_count: 0,
        inspiration_notes_error: "Zotero inspiration notes 暂不可用。"
      };
    }
  }

  async function openEvidence(chunkId, trace = null, origin = view) {
    if (!chunkId) return;
    setView("evidence");
    setReturnView(origin === "document" ? "document" : "search");
    setEvidenceState({ status: "loading", data: null, error: "" });
    selectEvidence(null, { ...(trace || {}), chunk_id: chunkId });
    try {
      const payload = await getJson(`/api/v1/library/evidence/${chunkId}`);
      setEvidenceState({ status: "ready", data: payload, error: "" });
      updateSafety(payload);
      if (payload.evidence?.document_id) loadZoteroCandidate(payload.evidence.document_id);
      loadEvidenceObjects(chunkId);
      selectEvidence(payload.evidence, trace);
    } catch (error) {
      setEvidenceState({
        status: "error",
        data: null,
        error: "证据不存在或本地 API 暂不可用。"
      });
    }
  }

  async function loadEvidenceObjects(chunkId) {
    if (!chunkId) return;
    setEvidenceObjectState((state) => ({
      byChunkId: {
        ...state.byChunkId,
        [chunkId]: { status: "loading", objects: [], error: "" }
      }
    }));
    try {
      const payload = await getJson(`/api/v1/library/evidence/${chunkId}/objects`);
      setEvidenceObjectState((state) => ({
        byChunkId: {
          ...state.byChunkId,
          [chunkId]: { status: "ready", objects: payload.objects || [], error: "" }
        }
      }));
      updateSafety(payload);
    } catch (error) {
      setEvidenceObjectState((state) => ({
        byChunkId: {
          ...state.byChunkId,
          [chunkId]: { status: "error", objects: [], error: "相关对象暂不可用。" }
        }
      }));
    }
  }

  async function openObject(objectKey, origin = view) {
    if (!objectKey) return;
    setView("object");
    setReturnView(origin);
    setObjectDetailState({ status: "loading", data: null, error: "" });
    setSourceTrace({ selection_type: "object", object_key: objectKey });
    try {
      const payload = await getJson(`/api/v1/library/objects/${encodeURIComponent(objectKey)}`);
      setObjectDetailState({ status: payload.status === "ok" ? "ready" : "empty", data: payload, error: payload.message || "" });
      updateSafety(payload);
      const object = payload.object || payload.objects?.[0];
      selectObject(object);
    } catch (error) {
      setObjectDetailState({ status: "error", data: null, error: "对象详情暂不可用。" });
    }
  }

  async function loadZoteroCandidate(documentId) {
    if (!documentId) return;
    const current = zoteroCandidateState.byDocumentId[documentId];
    if (current?.status === "loading" || current?.status === "ready") return;
    setZoteroCandidateState((state) => ({
      byDocumentId: {
        ...state.byDocumentId,
        [documentId]: { status: "loading", candidate: null, message: "" }
      }
    }));
    try {
      const payload = await getJson(`/api/v1/library/documents/${documentId}/zotero-link-candidates`);
      const candidate = selectTrustedZoteroCandidate(payload.candidates || []);
      setZoteroCandidateState((state) => ({
        byDocumentId: {
          ...state.byDocumentId,
          [documentId]: {
            status: "ready",
            candidate,
            message: candidate ? "" : zoteroCandidateMessage(payload.candidates || [])
          }
        }
      }));
      updateSafety(payload);
    } catch (error) {
      setZoteroCandidateState((state) => ({
        byDocumentId: {
          ...state.byDocumentId,
          [documentId]: { status: "error", candidate: null, message: "Zotero 候选暂不可用" }
        }
      }));
    }
  }

  function setSearchQueryState(updater) {
    clearSelection();
    setSearchState((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      return {
        ...next,
        papers: [],
        fallbackNotice: "",
        ...EMPTY_SIDECARS
      };
    });
  }

  return {
    readShelf,
    searchState,
    documentState,
    evidenceState,
    evidenceObjectState,
    objectDetailState,
    zoteroCandidateState,
    selectedDocumentId,
    setSearchQueryState,
    loadReadShelf,
    runSearch,
    openDocument,
    openEvidence,
    openObject,
  };
}

function isBookLikeChapteredDocument(document = {}) {
  return document?.object_import_mode === "chaptered";
}

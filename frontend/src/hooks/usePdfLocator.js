import { useState } from "react";
import { getJson } from "../api/client.js";
import { buildTrace } from "../utils/formatters.js";

export function usePdfLocator({ updateSafety, setSourceTrace }) {
  const [locatorState, setLocatorState] = useState({ byChunkId: {} });

  async function locateEvidence(chunkId, trace = null) {
    if (!chunkId) return;
    setLocatorState((state) => ({
      byChunkId: {
        ...state.byChunkId,
        [chunkId]: { status: "loading", payload: null, error: "" }
      }
    }));
    try {
      const params = new URLSearchParams();
      if (trace?.search_query) params.set("query", trace.search_query);
      (trace?.fallback_terms || []).slice(0, 12).forEach((term) => {
        if (term) params.append("fallback_terms", term);
      });
      if (trace?.title) params.append("fallback_terms", trace.title);
      if (trace?.snippet) params.append("fallback_terms", trace.snippet);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const payload = await getJson(`/api/v1/library/evidence/${chunkId}/pdf-location${suffix}`);
      setLocatorState((state) => ({
        byChunkId: {
          ...state.byChunkId,
          [chunkId]: { status: "ready", payload, error: "" }
        }
      }));
      updateSafety(payload);
      setSourceTrace((trace) => {
        if (!trace || trace.selection_type !== "evidence" || Number(trace.chunk_id) !== Number(chunkId)) return trace;
        return buildTrace(trace, { locator_result: payload.location });
      });
    } catch (error) {
      setLocatorState((state) => ({
        byChunkId: {
          ...state.byChunkId,
          [chunkId]: { status: "error", payload: null, error: "PDF 定位失败。" }
        }
      }));
    }
  }

  return {
    locatorState,
    locateEvidence,
  };
}

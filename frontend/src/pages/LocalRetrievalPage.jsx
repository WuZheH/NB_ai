import { useMemo, useState } from "react";
import EvidenceBasketPanel from "../components/retrieval/EvidenceBasketPanel.jsx";
import RetrievalFilters from "../components/retrieval/RetrievalFilters.jsx";
import RetrievalResultList from "../components/retrieval/RetrievalResultList.jsx";
import RetrievalSearchForm from "../components/retrieval/RetrievalSearchForm.jsx";
import {
  exportRetrievalEvidence,
  resolveRetrievalSelection,
  searchLocalRetrieval,
} from "../services/retrievalApi.js";

const ALL_SOURCE_TYPES = [
  "pdf_chunk",
  "zotero_highlight",
  "zotero_annotation_comment",
  "zotero_child_note",
  "zotero_inspiration_note",
  "personal_note",
  "markdown_note",
];
const NOTE_SOURCE_TYPES = ALL_SOURCE_TYPES.filter((sourceType) => sourceType !== "pdf_chunk");
const DEFAULT_FILTERS = {
  sourceType: "",
  documentId: "",
  year: "",
  includeContext: true,
  collapseDuplicates: true,
};
const DEFAULT_EXPORT_OPTIONS = {
  include_context_before: true,
  include_context_after: true,
  include_note_comment: true,
  include_match_reasons: true,
  include_provenance: true,
  include_raw_warnings: false,
  group_by_document: false,
};

export default function LocalRetrievalPage() {
  const [query, setQuery] = useState("spectral clustering");
  const [mode, setMode] = useState("precision");
  const [limit, setLimit] = useState(50);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [searchState, setSearchState] = useState({ status: "idle", data: null, error: "" });
  const [lastSearchRequest, setLastSearchRequest] = useState(null);
  const [basket, setBasket] = useState([]);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportOptions, setExportOptions] = useState(DEFAULT_EXPORT_OPTIONS);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const selectedIds = useMemo(
    () => new Set(basket.map((item) => item.fragment_id)),
    [basket]
  );
  const aliases = searchState.data?.query_plan?.curated_aliases || [];

  async function runSearch(event) {
    event?.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;
    const request = buildSearchRequest({ query: trimmedQuery, mode, limit, filters });
    setSearchState({ status: "loading", data: null, error: "" });
    setError("");
    try {
      const data = await searchLocalRetrieval(request);
      setLastSearchRequest(request);
      setSearchState({ status: "ready", data, error: "" });
    } catch (requestError) {
      setSearchState({ status: "error", data: null, error: apiErrorMessage(requestError) });
    }
  }

  function addItems(items) {
    setBasket((current) => {
      const existing = new Set(current.map((item) => item.fragment_id));
      const additions = [];
      items.forEach((item) => {
        if (!item?.fragment_id || existing.has(item.fragment_id)) return;
        existing.add(item.fragment_id);
        additions.push(normalizeBasketItem(item));
      });
      return reindexBasket([...current, ...additions]);
    });
    setNotice("证据篮子已更新。");
    setError("");
  }

  function toggleItem(item) {
    if (selectedIds.has(item.fragment_id)) {
      removeItem(item.fragment_id);
    } else {
      addItems([item]);
    }
  }

  function removeItem(fragmentId) {
    setBasket((current) => reindexBasket(
      current.filter((item) => item.fragment_id !== fragmentId)
    ));
  }

  function clearPageSelection() {
    const pageIds = new Set((searchState.data?.results || []).map((item) => item.fragment_id));
    setBasket((current) => reindexBasket(
      current.filter((item) => !pageIds.has(item.fragment_id))
    ));
    setNotice("已清除当前页选择。");
  }

  function moveItem(index, direction) {
    setBasket((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return reindexBasket(next);
    });
  }

  async function addAllSearchResults() {
    if (!lastSearchRequest || selectionBusy) return;
    setSelectionBusy(true);
    setError("");
    try {
      const response = await resolveRetrievalSelection({
        type: "search_results",
        search_request: lastSearchRequest,
        max_items: 500,
      });
      addItems(response.items);
      setNotice(`已解析并加入 ${response.resolved_count} 条搜索结果。`);
    } catch (requestError) {
      setError(apiErrorMessage(requestError));
    } finally {
      setSelectionBusy(false);
    }
  }

  async function addDocumentScope(documentId, noteOnly) {
    if (!documentId || selectionBusy) return;
    setSelectionBusy(true);
    setError("");
    try {
      const response = await resolveRetrievalSelection({
        type: "document_scope",
        document_id: Number(documentId),
        source_types: noteOnly ? NOTE_SOURCE_TYPES : ALL_SOURCE_TYPES,
        max_items: 1000,
      });
      addItems(response.items);
      setNotice(`文档 ${documentId} 已加入 ${response.resolved_count} 条证据。`);
    } catch (requestError) {
      setError(apiErrorMessage(requestError));
    } finally {
      setSelectionBusy(false);
    }
  }

  async function handleExport(format, action) {
    if (!basket.length || exportBusy) return;
    setExportBusy(true);
    setError("");
    try {
      const response = await exportRetrievalEvidence({
        fragment_ids: basket.map((item) => item.fragment_id),
        format,
        query: lastSearchRequest?.query || query.trim() || null,
        retrieval_mode: lastSearchRequest?.mode || mode,
        options: exportOptions,
        save_to_file: false,
      });
      if (action === "copy") {
        if (!navigator.clipboard?.writeText) {
          throw new Error("当前浏览器不提供剪贴板写入能力。");
        }
        await navigator.clipboard.writeText(response.content);
        setNotice(`已复制 ${response.evidence_count} 条 Markdown 证据。`);
      } else {
        downloadContent(response.content, response.filename, response.mime_type);
        setNotice(`已生成 ${response.filename}。`);
      }
      if (response.warnings?.length) {
        setNotice((current) => `${current} ${response.warnings.join(" · ")}`);
      }
    } catch (requestError) {
      const failureLabel = action === "copy" ? "复制失败" : "导出失败";
      setError(`${failureLabel}：${apiErrorMessage(requestError)}`);
    } finally {
      setExportBusy(false);
    }
  }

  return (
    <main className="localRetrievalPage">
      <header className="localRetrievalHeader">
        <div>
          <span>LOCAL RETRIEVAL</span>
          <h1>本地证据检索</h1>
        </div>
        <div className="localRetrievalIndexState">
          <span className={searchState.data?.index_status?.status === "ready" ? "ready" : ""} />
          {searchState.data?.index_status?.status || "等待检索"}
          {searchState.data?.index_status?.index_content_hash && (
            <code>{searchState.data.index_status.index_content_hash.slice(0, 12)}</code>
          )}
        </div>
      </header>

      <RetrievalSearchForm
        query={query}
        mode={mode}
        limit={limit}
        loading={searchState.status === "loading"}
        onQueryChange={setQuery}
        onModeChange={setMode}
        onLimitChange={setLimit}
        onSubmit={runSearch}
      />
      <RetrievalFilters value={filters} onChange={setFilters} />

      {aliases.length > 0 && (
        <div className="localRetrievalAliases">
          {aliases.map((alias) => (
            <span key={`${alias.concept}-${alias.matched_term}`}>
              curated_alias_match · {alias.concept} · {alias.expanded_terms.join(" / ")}
            </span>
          ))}
        </div>
      )}

      <div className="localRetrievalBody">
        <RetrievalResultList
          state={searchState}
          selectedIds={selectedIds}
          onToggle={toggleItem}
          onAddPage={() => addItems(searchState.data?.results || [])}
          onAddAll={addAllSearchResults}
          onClearPage={clearPageSelection}
          onAddDocument={(documentId) => addDocumentScope(documentId, false)}
          onAddDocumentNotes={(documentId) => addDocumentScope(documentId, true)}
        />
        <EvidenceBasketPanel
          items={basket}
          exportOptions={exportOptions}
          exportBusy={exportBusy || selectionBusy}
          notice={notice}
          error={error}
          onRemove={removeItem}
          onClear={() => { setBasket([]); setNotice("证据篮子已清空。"); }}
          onMove={moveItem}
          onExportOptionsChange={setExportOptions}
          onExport={handleExport}
        />
      </div>
    </main>
  );
}

export function buildSearchRequest({ query, mode, limit, filters }) {
  const structuredFilters = {};
  if (filters.sourceType) structuredFilters.source_type = filters.sourceType;
  if (positiveInteger(filters.documentId)) structuredFilters.document_id = Number(filters.documentId);
  if (positiveInteger(filters.year)) structuredFilters.year = Number(filters.year);
  return {
    query,
    mode,
    limit,
    offset: 0,
    collapse_duplicates: Boolean(filters.collapseDuplicates),
    include_context: Boolean(filters.includeContext),
    filters: structuredFilters,
  };
}

export function normalizeBasketItem(item) {
  return {
    fragment_id: item.fragment_id,
    display_id: item.display_id,
    source_type: item.source_type,
    origin_kind: item.origin_kind,
    document_id: item.document_id ?? null,
    title: item.title ?? null,
    authors: Array.isArray(item.authors) ? item.authors : [],
    year: item.year ?? null,
    page_number: item.page_number ?? null,
    page_label: item.page_label ?? null,
    section: item.section ?? null,
    duplicate_count: item.duplicate_count || 1,
    warnings: Array.isArray(item.warnings) ? item.warnings : [],
    selected_order: Number(item.selected_order) || 0,
  };
}

export function reindexBasket(items) {
  return items.map((item, index) => ({ ...item, selected_order: index + 1 }));
}

export function apiErrorMessage(error) {
  const detail = error?.payload?.detail;
  if (detail?.message) {
    const counts = detail.available_count && detail.max_items
      ? ` (${detail.available_count} / ${detail.max_items})`
      : "";
    return `${detail.message}${counts}`;
  }
  return detail?.error || error?.message || "本地检索 API 不可用。";
}

export function downloadContent(content, filename, mimeType) {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function positiveInteger(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0;
}

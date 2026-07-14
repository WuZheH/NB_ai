import { useEffect, useMemo, useRef, useState } from "react";
import EvidenceBasketPanel from "../components/retrieval/EvidenceBasketPanel.jsx";
import RetrievalFilters from "../components/retrieval/RetrievalFilters.jsx";
import RetrievalResultList from "../components/retrieval/RetrievalResultList.jsx";
import RetrievalSearchForm from "../components/retrieval/RetrievalSearchForm.jsx";
import SearchPreviewPanel from "../features/retrieval/components/SearchPreviewPanel.jsx";
import {
  exportRetrievalEvidence,
  fetchRetrievalFragment,
  fetchRetrievalFragmentLocator,
  resolveRetrievalSelection,
  searchLocalRetrieval,
  searchNotebookRetrieval,
} from "../services/retrievalApi.js";
import {
  HIGH_QUALITY_SEARCH_KIND,
  KEYWORD_SEARCH_KIND,
  buildKeywordSearchRequest,
  buildNotebookSearchRequest as createNotebookSearchRequest,
  fragmentFromResponse,
  normalizeRetrievalResponse,
} from "../features/retrieval/utils/notebookSearch.js";
import {
  copyTextToClipboard,
  downloadTextFile,
} from "../features/retrieval/utils/retrievalResults.js";

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
  const [query, setQuery] = useState("");
  const [searchKind, setSearchKind] = useState(HIGH_QUALITY_SEARCH_KIND);
  const [ftsMode, setFtsMode] = useState("precision");
  const [limit, setLimit] = useState(12);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [searchState, setSearchState] = useState({ status: "idle", data: null, error: "" });
  const [previewState, setPreviewState] = useState({ status: "idle", data: null, error: "" });
  const [lastSearchRequest, setLastSearchRequest] = useState(null);
  const [basket, setBasket] = useState([]);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportOptions, setExportOptions] = useState(DEFAULT_EXPORT_OPTIONS);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const previewRequestRef = useRef(null);

  useEffect(() => () => previewRequestRef.current?.abort(), []);

  const selectedIds = useMemo(
    () => new Set(basket.map((item) => item.fragment_id)),
    [basket]
  );
  const aliases = searchKind === KEYWORD_SEARCH_KIND
    ? searchState.data?.query_plan?.curated_aliases || []
    : [];

  async function runSearch(event) {
    event?.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;
    const request = searchKind === HIGH_QUALITY_SEARCH_KIND
      ? createNotebookSearchRequest({ query: trimmedQuery, limit, filters })
      : buildKeywordSearchRequest({ query: trimmedQuery, mode: ftsMode, limit, filters });
    setSearchState({ status: "loading", data: null, error: "" });
    setError("");
    try {
      const response = searchKind === HIGH_QUALITY_SEARCH_KIND
        ? await searchNotebookRetrieval(request)
        : await searchLocalRetrieval(request);
      const data = normalizeRetrievalResponse(response);
      setLastSearchRequest({ kind: searchKind, request });
      setSearchState({ status: "ready", data, error: "" });
      setPreviewState({ status: "idle", data: null, error: "" });
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

  async function copyResult(item) {
    try {
      const text = copyableFragmentText(item);
      if (!text) throw new Error("该结果没有可复制的片段正文。");
      await copyTextToClipboard(text);
      setNotice("已复制片段正文。");
      setError("");
    } catch (copyError) {
      setError(`复制失败：${apiErrorMessage(copyError)}`);
    }
  }

  async function previewFragment(item) {
    previewRequestRef.current?.abort();
    const controller = new AbortController();
    previewRequestRef.current = controller;
    setPreviewState({ status: "loading_fragment", data: item, error: "" });
    try {
      const response = await fetchRetrievalFragment(item.fragment_id, { signal: controller.signal });
      const detail = fragmentFromResponse(response);
      let locator = null;
      let locatorError = "";
      try {
        locator = await fetchRetrievalFragmentLocator(item.fragment_id, { signal: controller.signal });
      } catch (locatorRequestError) {
        if (controller.signal.aborted) return;
        locatorError = apiErrorMessage(locatorRequestError);
      }
      if (controller.signal.aborted || previewRequestRef.current !== controller) return;
      setPreviewState({
        status: "ready",
        data: {
          ...item,
          ...detail,
          final_rank: item.final_rank,
          final_score: item.final_score,
          reranker_score: item.reranker_score,
          semantic_score: item.semantic_score,
          locator,
          locator_error: locatorError,
        },
        error: "",
      });
    } catch (requestError) {
      if (!controller.signal.aborted && previewRequestRef.current === controller) {
        setPreviewState({ status: "error", data: item, error: apiErrorMessage(requestError) });
      }
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
    if (lastSearchRequest.kind === HIGH_QUALITY_SEARCH_KIND) {
      const results = searchState.data?.results || [];
      addItems(results);
      setNotice(`已加入当前高质量搜索返回的 ${results.length} 条证据。`);
      return;
    }
    setSelectionBusy(true);
    setError("");
    try {
      const response = await resolveRetrievalSelection({
        type: "search_results",
        search_request: lastSearchRequest.request,
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

  async function handleExport(format, action) {
    if (!basket.length || exportBusy) return;
    setExportBusy(true);
    setError("");
    try {
      const exportRequest = {
        fragment_ids: basket.map((item) => item.fragment_id),
        format,
        query: lastSearchRequest?.request?.query || query.trim() || null,
        options: exportOptions,
        save_to_file: false,
      };
      if (lastSearchRequest?.kind === KEYWORD_SEARCH_KIND) {
        exportRequest.retrieval_mode = lastSearchRequest.request.mode || ftsMode;
      }
      const response = await exportRetrievalEvidence(exportRequest);
      if (action === "copy") {
        await copyTextToClipboard(response.content);
        setNotice(`已复制 ${response.evidence_count} 条 Markdown 证据。`);
      } else {
        downloadTextFile(response.filename, response.content, response.mime_type);
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
          <span>SEARCH</span>
          <h1>搜索资料与阅读笔记</h1>
        </div>
        <div className="localRetrievalIndexState">
          <span className={
            searchState.data?.status === "ok" || searchState.data?.index_status?.status === "ready"
              ? "ready"
              : ""
          } />
          {searchKind === HIGH_QUALITY_SEARCH_KIND
            ? searchState.data?.backend || "等待高质量检索"
            : searchState.data?.index_status?.status || "等待关键词检索"}
          {searchKind === HIGH_QUALITY_SEARCH_KIND && searchState.data?.reranker_model && (
            <code>{searchState.data.reranker_model}</code>
          )}
          {searchKind === KEYWORD_SEARCH_KIND && searchState.data?.index_status?.index_content_hash && (
            <code>{searchState.data.index_status.index_content_hash.slice(0, 12)}</code>
          )}
        </div>
      </header>

      <section className="localRetrievalToolbar" aria-label="搜索工具栏">
        <RetrievalSearchForm
          query={query}
          searchKind={searchKind}
          ftsMode={ftsMode}
          limit={limit}
          loading={searchState.status === "loading"}
          onQueryChange={setQuery}
          onSearchKindChange={setSearchKind}
          onFtsModeChange={setFtsMode}
          onLimitChange={setLimit}
          onSubmit={runSearch}
        />
        <RetrievalFilters value={filters} searchKind={searchKind} onChange={setFilters} />
      </section>

      {aliases.length > 0 && (
        <div className="localRetrievalAliases">
          {aliases.map((alias) => (
            <span key={`${alias.concept}-${alias.matched_term}`}>
              curated_alias_match · {alias.concept} · {alias.expanded_terms.join(" / ")}
            </span>
          ))}
        </div>
      )}

      {searchState.data?.warnings?.length > 0 && (
        <div className="localRetrievalWarnings" role="status">
          {searchState.data.warnings.map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      )}

      <div className="localRetrievalBody searchResultWorkspace" data-testid="retrieval-workspace">
        <RetrievalResultList
          state={searchState}
          searchKind={searchKind}
          selectedIds={selectedIds}
          onToggle={toggleItem}
          onPreview={previewFragment}
          onCopy={copyResult}
          onCopiedId={() => setNotice("已复制完整 fragment ID。")}
          onAddPage={() => addItems(searchState.data?.results || [])}
          onAddAll={addAllSearchResults}
          onClearPage={clearPageSelection}
        />
        <div className={[
          "searchResultRail",
          previewState.status === "idle" ? "" : "hasPreview",
          basket.length ? "hasBasket" : "",
          previewState.status === "idle" && !basket.length ? "isDormant" : "",
        ].filter(Boolean).join(" ")}>
          <SearchPreviewPanel
            state={previewState}
              onClose={() => {
                previewRequestRef.current?.abort();
                setPreviewState({ status: "idle", data: null, error: "" });
              }}
            onCopyFragment={copyResult}
            onCopiedId={() => setNotice("已复制完整 fragment ID。")}
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
    display_id: item.display_id || item.fragment_id,
    source_type: item.source_type,
    origin_kind: item.origin_kind ?? null,
    document_id: item.document_id ?? null,
    title: item.document_title ?? item.title ?? null,
    authors: Array.isArray(item.authors) ? item.authors : [],
    year: item.year ?? null,
    page_number: item.pdf_page ?? item.page_number ?? null,
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
  if (typeof detail === "string") return detail;
  if (detail?.message) {
    const counts = detail.available_count && detail.max_items
      ? ` (${detail.available_count} / ${detail.max_items})`
      : "";
    return `${detail.message}${counts}`;
  }
  return detail?.error || error?.message || "本地检索 API 不可用。";
}

export function downloadContent(content, filename, mimeType) {
  return downloadTextFile(filename, content, `${mimeType};charset=utf-8`);
}

function positiveInteger(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0;
}

export function copyableFragmentText(item = {}) {
  if (item.source_type === "pdf_chunk") return String(item.text || "").trim();
  return [item.note_text, item.selected_text].map((value) => String(value || "").trim()).filter(Boolean).join("\n\n");
}

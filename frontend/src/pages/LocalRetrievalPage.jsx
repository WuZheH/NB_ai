import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import EvidenceBasketPanel from "../components/retrieval/EvidenceBasketPanel.jsx";
import RetrievalFilters from "../components/retrieval/RetrievalFilters.jsx";
import RetrievalResultList from "../components/retrieval/RetrievalResultList.jsx";
import RetrievalSearchForm from "../components/retrieval/RetrievalSearchForm.jsx";
import SearchPreviewPanel from "../features/retrieval/components/SearchPreviewPanel.jsx";
import {
  exportRetrievalEvidence,
  fetchEvidencePdfLocation,
  fetchRetrievalIndexStatus,
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
import {
  readSearchSession,
  registerSearchSessionCapture,
  writeSearchSession,
} from "../features/retrieval/state/searchSession.js";

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
  const initialSessionRef = useRef(readSearchSession() || {});
  const initialSession = initialSessionRef.current;
  const pageRef = useRef(null);
  const latestSessionRef = useRef(initialSession);
  const pendingScrollRestoreRef = useRef(initialSession.scroll || null);
  const [query, setQuery] = useState(initialSession.query || "");
  const [searchKind, setSearchKind] = useState(initialSession.searchKind || HIGH_QUALITY_SEARCH_KIND);
  const [ftsMode, setFtsMode] = useState(initialSession.ftsMode || "precision");
  const [limit, setLimit] = useState(initialSession.limit || 12);
  const [filters, setFilters] = useState({ ...DEFAULT_FILTERS, ...(initialSession.filters || {}) });
  const [searchState, setSearchState] = useState(initialSession.searchState || { status: "idle", data: null, error: "" });
  const [previewState, setPreviewState] = useState(initialSession.previewState || { status: "idle", data: null, error: "" });
  const [lastSearchRequest, setLastSearchRequest] = useState(initialSession.lastSearchRequest || null);
  const [basket, setBasket] = useState(initialSession.basket || []);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportOptions, setExportOptions] = useState({ ...DEFAULT_EXPORT_OPTIONS, ...(initialSession.exportOptions || {}) });
  const [notice, setNotice] = useState(initialSession.notice || "");
  const [error, setError] = useState(initialSession.error || "");
  const [indexState, setIndexState] = useState({ status: "loading", data: null });
  const previewRequestRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchRetrievalIndexStatus({ signal: controller.signal })
      .then((data) => setIndexState({ status: "ready", data }))
      .catch(() => {
        if (!controller.signal.aborted) setIndexState({ status: "unavailable", data: null });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const root = pageRef.current;
    const rememberScroll = () => {
      const scroll = captureSearchScroll(root);
      if (!scroll) return;
      const pdfPreviewState = capturePdfPreviewState(root);
      const currentPreview = latestSessionRef.current.previewState;
      const previewState = pdfPreviewState && currentPreview?.status === "ready" && currentPreview.data
        ? { ...currentPreview, data: { ...currentPreview.data, pdf_preview_state: pdfPreviewState } }
        : currentPreview;
      const session = { ...latestSessionRef.current, scroll, previewState };
      latestSessionRef.current = session;
      writeSearchSession(session);
    };
    const unregisterCapture = registerSearchSessionCapture(rememberScroll);
    root?.addEventListener("scroll", rememberScroll, true);
    return () => {
      unregisterCapture();
      root?.removeEventListener("scroll", rememberScroll, true);
      previewRequestRef.current?.abort();
      writeSearchSession(latestSessionRef.current);
    };
  }, []);

  useLayoutEffect(() => {
    if (!pendingScrollRestoreRef.current) return undefined;
    let frame = 0;
    let attempts = 0;
    const restoreWhenScrollable = () => {
      const restored = restoreSearchScroll(pageRef.current, pendingScrollRestoreRef.current);
      attempts += 1;
      if (restored || attempts >= 60) {
        pendingScrollRestoreRef.current = null;
        return;
      }
      frame = requestAnimationFrame(restoreWhenScrollable);
    };
    frame = requestAnimationFrame(restoreWhenScrollable);
    return () => cancelAnimationFrame(frame);
  }, [searchState.data, previewState.data, basket.length]);

  useEffect(() => {
    const session = {
      query,
      searchKind,
      ftsMode,
      limit,
      filters,
      searchState,
      previewState,
      lastSearchRequest,
      basket,
      exportOptions,
      notice,
      error,
      scroll: latestSessionRef.current?.scroll || initialSession.scroll || null,
    };
    latestSessionRef.current = session;
    writeSearchSession(session);
  }, [query, searchKind, ftsMode, limit, filters, searchState, previewState, lastSearchRequest, basket, exportOptions, notice, error]);

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
      let pdfLocation = null;
      try {
        locator = await fetchRetrievalFragmentLocator(item.fragment_id, { signal: controller.signal });
      } catch (locatorRequestError) {
        if (controller.signal.aborted) return;
        locatorError = apiErrorMessage(locatorRequestError);
      }
      const chunkId = detail?.chunk_id ?? item?.chunk_id;
      if (chunkId) {
        try {
          pdfLocation = await fetchEvidencePdfLocation(chunkId, { signal: controller.signal });
        } catch (pdfLocationError) {
          if (controller.signal.aborted) return;
          locatorError = locatorError || apiErrorMessage(pdfLocationError);
        }
      }
      if (controller.signal.aborted || previewRequestRef.current !== controller) return;
      setPreviewState({
        status: "ready",
        data: {
          ...item,
          ...detail,
          selection_rank: item.selection_rank,
          locator,
          pdf_location: pdfLocation,
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
    <main className="localRetrievalPage" ref={pageRef}>
      <header className="localRetrievalHeader">
        <div>
          <span>SEARCH</span>
          <h1>搜索</h1>
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

      {indexState.status === "ready" && indexState.data?.data_state === "empty_library" && (
        <section className="localRetrievalEmptyLibrary" role="status" data-testid="empty-library-state">
          <strong>资料库为空</strong>
          <span>请导入 PDF，或通过 SEARCH_DATA_DIR 配置已有数据目录。Search 不会自动创建生产索引。</span>
        </section>
      )}

      {indexState.status === "ready"
        && indexState.data?.data_state !== "empty_library"
        && !indexState.data?.ready && (
          <section className="localRetrievalEmptyLibrary" role="status">
            <strong>搜索索引尚未准备</strong>
            <span>资料库已配置，但关键词索引不可用。请按 README 检查索引状态。</span>
          </section>
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
            onViewChange={(view) => setPreviewState((current) => current.status === "ready"
              ? { ...current, data: { ...current.data, preview_view: view } }
              : current)}
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

function captureSearchScroll(root) {
  if (!root) return null;
  return {
    results: root.querySelector('[data-testid="retrieval-results-scroll"]')?.scrollTop || 0,
    preview: root.querySelector('[data-testid="search-preview-scroll"]')?.scrollTop || 0,
    basket: root.querySelector('[data-testid="evidence-basket-scroll"]')?.scrollTop || 0,
  };
}

function capturePdfPreviewState(root) {
  const preview = root?.querySelector('[data-testid="pdf-location-preview"]');
  const scroller = root?.querySelector('.searchPreviewPdfStage .pdfPreviewScroller');
  if (!preview || !scroller || preview.dataset.previewReady !== "true") return null;
  const state = {
    document_id: Number(preview.dataset.documentId),
    chunk_id: Number(preview.dataset.chunkId),
    requested_page_number: Number(preview.dataset.requestedPageNumber),
    scale: Number(preview.dataset.renderScale),
    scroll_top: Number(scroller.scrollTop),
    scroll_left: Number(scroller.scrollLeft),
  };
  if (
    !Number.isInteger(state.document_id)
    || state.document_id < 1
    || !Number.isInteger(state.requested_page_number)
    || state.requested_page_number < 1
    || !Number.isFinite(state.scale)
    || state.scale <= 0
    || !Number.isFinite(state.scroll_top)
    || !Number.isFinite(state.scroll_left)
  ) return null;
  return state;
}

function restoreSearchScroll(root, scroll) {
  if (!root || !scroll) return false;
  const pairs = [
    ['[data-testid="retrieval-results-scroll"]', scroll.results],
    ['[data-testid="search-preview-scroll"]', scroll.preview],
    ['[data-testid="evidence-basket-scroll"]', scroll.basket],
  ];
  return pairs.every(([selector, value]) => {
    const element = root.querySelector(selector);
    const target = Number(value);
    if (!element || !Number.isFinite(target)) return false;
    element.scrollTop = target;
    return Math.abs(element.scrollTop - target) <= 1;
  });
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
  if (item.source_type === "pdf_chunk") return String(item.coherent_text || "").trim();
  return [item.user_note, item.selected_source_text].map((value) => String(value || "").trim()).filter(Boolean).join("\n\n");
}

import { useEffect, useMemo, useState } from "react";

import { EvidenceBasket } from "./components/EvidenceBasket";
import { ResultCard } from "./components/ResultCard";
import { SourceFilters } from "./components/SourceFilters";
import { StatePanel } from "./components/StatePanel";
import { NOTEBOOK_SOURCES } from "./constants";
import { pinnedEvidence, selectedEvidence, toggleEvidence } from "./state/evidenceSelection";
import { mcpBridge } from "./state/mcpBridge";
import { exportedContent, fetchedFragment, searchViewModel } from "./state/toolData";
import { createWidgetState, readHostWidgetState, retainAvailableIds } from "./state/widgetState";
import type { EvidenceFormat, FragmentDetail, SearchResult, SourceType, ToolEnvelope } from "./types";

function hasSearchPayload(envelope: ToolEnvelope): boolean {
  const structured = envelope.structuredContent ?? {};
  const meta = envelope._meta ?? {};
  return Array.isArray(structured.results) || Array.isArray(meta["notebookAi/results"]);
}

async function copyText(value: string): Promise<void> {
  if (!navigator.clipboard?.writeText) {
    throw new Error("当前宿主没有开放剪贴板权限；可从下方导出预览手动复制。 ");
  }
  await navigator.clipboard.writeText(value);
}

export default function App() {
  const [initialWidgetState] = useState(() => readHostWidgetState());
  const [view, setView] = useState(() => {
    const initialEnvelope = mcpBridge.initialEnvelope();
    return hasSearchPayload(initialEnvelope ?? {}) ? searchViewModel(initialEnvelope) : searchViewModel(null);
  });
  const [activeSources, setActiveSources] = useState<Set<SourceType>>(
    () => new Set(initialWidgetState.activeSources),
  );
  const [selectedIds, setSelectedIds] = useState<string[]>(initialWidgetState.selectedIds);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set(initialWidgetState.expandedIds));
  const [details, setDetails] = useState<Record<string, FragmentDetail>>({});
  const [loadingDetail, setLoadingDetail] = useState<Set<string>>(() => new Set());
  const [exporting, setExporting] = useState(false);
  const [basketStatus, setBasketStatus] = useState("");
  const [exportPreview, setExportPreview] = useState("");

  useEffect(
    () =>
      mcpBridge.subscribe((envelope) => {
        if (hasSearchPayload(envelope)) setView(searchViewModel(envelope));
      }),
    [],
  );

  const displayedResults = useMemo(
    () => view.results.filter((result) => activeSources.has(result.source_type)),
    [view.results, activeSources],
  );
  const selected = useMemo(() => selectedEvidence(view.results, selectedIds), [view.results, selectedIds]);

  useEffect(() => {
    void mcpBridge.persistWidgetState(createWidgetState(selectedIds, activeSources, expandedIds));
  }, [activeSources, expandedIds, selectedIds]);

  useEffect(() => {
    if (view.status === "loading") return;
    const availableIds = new Set(view.results.map((result) => result.fragment_id));
    setSelectedIds((current) => retainAvailableIds(current, availableIds));
    setExpandedIds((current) => new Set(retainAvailableIds([...current], availableIds)));
    setDetails((current) =>
      Object.fromEntries(Object.entries(current).filter(([fragmentId]) => availableIds.has(fragmentId))),
    );
  }, [view.results, view.status]);

  function toggleSource(sourceType: SourceType) {
    setActiveSources((current) => {
      const next = new Set(current);
      if (next.has(sourceType)) next.delete(sourceType);
      else next.add(sourceType);
      return next;
    });
  }

  async function toggleExpanded(result: SearchResult) {
    const isExpanded = expandedIds.has(result.fragment_id);
    setExpandedIds((current) => {
      const next = new Set(current);
      if (isExpanded) next.delete(result.fragment_id);
      else next.add(result.fragment_id);
      return next;
    });
    if (isExpanded || details[result.fragment_id]) return;

    setLoadingDetail((current) => new Set(current).add(result.fragment_id));
    try {
      const envelope = await mcpBridge.callTool("fetch", { fragment_id: result.fragment_id });
      const fragment = fetchedFragment(envelope);
      if (fragment) setDetails((current) => ({ ...current, [result.fragment_id]: fragment }));
      else throw new Error("fetch 没有返回片段。 ");
    } catch (error) {
      setBasketStatus(error instanceof Error ? error.message : "读取完整片段失败。 ");
    } finally {
      setLoadingDetail((current) => {
        const next = new Set(current);
        next.delete(result.fragment_id);
        return next;
      });
    }
  }

  async function exportSelection(format: EvidenceFormat, fragmentIds = selectedIds) {
    if (!fragmentIds.length) return;
    setExporting(true);
    setBasketStatus("正在准备导出…");
    try {
      const envelope = await mcpBridge.callTool("export_evidence", {
        fragment_ids: fragmentIds,
        format,
        query: view.query,
      });
      const content = exportedContent(envelope);
      if (!content) throw new Error("导出工具没有返回可复制内容。 ");
      setExportPreview(content);
      try {
        await copyText(content);
        setBasketStatus(`${format.toUpperCase()} 已复制到剪贴板。`);
      } catch (error) {
        setBasketStatus(error instanceof Error ? error.message : "请从导出预览手动复制。 ");
      }
    } catch (error) {
      setBasketStatus(error instanceof Error ? error.message : "证据导出失败。 ");
    } finally {
      setExporting(false);
    }
  }

  async function pinSelection() {
    const compactEvidence = pinnedEvidence(view.results, selectedIds);
    if (!compactEvidence.length) return;
    try {
      await mcpBridge.pinSelection(compactEvidence);
      setBasketStatus("已将紧凑证据定位信息固定到当前聊天；未发送正文或 provenance。");
    } catch (error) {
      setBasketStatus(error instanceof Error ? error.message : "当前宿主不支持固定到聊天。");
    }
  }

  async function copyFragment(result: SearchResult) {
    const content = result.source_type === "pdf_chunk"
      ? String(result.text || "").trim()
      : [result.note_text, result.selected_text].map((value) => String(value || "").trim()).filter(Boolean).join("\n\n");
    if (!content) {
      setBasketStatus("该结果没有可复制的片段正文。");
      return;
    }
    try {
      await copyText(content);
      setBasketStatus("已复制片段正文。");
    } catch (error) {
      setBasketStatus(error instanceof Error ? error.message : "复制片段失败。");
    }
  }

  async function copyFragmentId(fragmentId: string) {
    try {
      await copyText(fragmentId);
      setBasketStatus("已复制完整 fragment ID。");
    } catch (error) {
      setBasketStatus(error instanceof Error ? error.message : "复制 fragment ID 失败。");
    }
  }

  return (
    <main className="app-shell search-host-theme-aware">
      <header className="app-header">
        <div>
          <p className="eyebrow">Search · 高质量资料搜索</p>
          <h1>研究资料检索</h1>
        </div>
      </header>

      <section className="search-summary" aria-label="检索摘要">
        <strong>{view.query || "等待检索问题"}</strong>
        <span>{displayedResults.length} / {view.resultCount} 条可见结果</span>
        <span>PDF 原文与 Zotero 用户笔记保持独立</span>
      </section>

      <SourceFilters active={activeSources} onToggle={toggleSource} />
      <div className="widget-scroll-region search-scroll-region" role="region" aria-label="Search 检索结果">
        {view.warnings.length > 0 && (
          <div className="warnings" role="status">{view.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>
        )}

        {view.error ? (
          <StatePanel kind="error">{view.error}</StatePanel>
        ) : view.status === "loading" ? (
          <StatePanel kind="loading">正在等待 Search 检索结果…</StatePanel>
        ) : displayedResults.length === 0 ? (
          <StatePanel kind="empty">当前来源筛选下没有结果。</StatePanel>
        ) : (
          <section className="results" aria-label="检索结果">
            {displayedResults.map((baseResult, index) => {
              const detail = details[baseResult.fragment_id];
              const result = detail ? { ...baseResult, ...detail } : baseResult;
              const previous = displayedResults[index - 1];
              return (
                <ResultCard
                  key={result.fragment_id}
                  result={result}
                  selected={selectedIds.includes(result.fragment_id)}
                  expanded={expandedIds.has(result.fragment_id)}
                  loadingDetail={loadingDetail.has(result.fragment_id)}
                  showDocumentTitle={!previous || previous.document_id !== result.document_id}
                  onSelect={() => setSelectedIds((current) => toggleEvidence(current, result.fragment_id))}
                  onExpand={() => void toggleExpanded(result)}
                  onCopyFragment={() => void copyFragment(result)}
                  onCopyId={() => void copyFragmentId(result.fragment_id)}
                />
              );
            })}
          </section>
        )}

        {exportPreview && (
          <section className="export-preview">
            <div>
              <h2>导出预览</h2>
              <button type="button" className="search-button search-button-transparent search-button-compact" onClick={() => setExportPreview("")}>关闭</button>
            </div>
            <textarea readOnly value={exportPreview} aria-label="导出内容" />
          </section>
        )}
      </div>

      <EvidenceBasket
        selected={selected}
        selectedCount={selectedIds.length}
        canPin={selected.length > 0 && selected.length === selectedIds.length}
        exporting={exporting}
        status={basketStatus}
        onClear={() => setSelectedIds([])}
        onExport={(format) => void exportSelection(format)}
        onPin={() => void pinSelection()}
      />

    </main>
  );
}

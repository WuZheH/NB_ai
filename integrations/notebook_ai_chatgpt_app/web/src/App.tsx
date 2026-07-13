import { useEffect, useMemo, useState } from "react";

import { EvidenceBasket } from "./components/EvidenceBasket";
import { ResultCard } from "./components/ResultCard";
import { SourceFilters } from "./components/SourceFilters";
import { StatePanel } from "./components/StatePanel";
import { NOTEBOOK_SOURCES } from "./constants";
import { selectedEvidence, selectionContext, toggleEvidence } from "./state/evidenceSelection";
import { mcpBridge } from "./state/mcpBridge";
import { exportedContent, fetchedFragment, searchViewModel } from "./state/toolData";
import type { EvidenceFormat, SearchResult, SourceType, ToolEnvelope } from "./types";

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
  const [view, setView] = useState(() => searchViewModel(mcpBridge.initialEnvelope()));
  const [activeSources, setActiveSources] = useState<Set<SourceType>>(() => new Set(NOTEBOOK_SOURCES));
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [details, setDetails] = useState<Record<string, SearchResult>>({});
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
    void mcpBridge.updateModelContext(selectionContext(view.results, selectedIds), selectedIds);
  }, [view.results, selectedIds]);

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

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">NOTEBOOK_AI · 高质量资料搜索</p>
          <h1>{view.query || "研究资料检索"}</h1>
          <p>{view.resultCount} 条结果 · PDF 原文与 Zotero 用户笔记保持独立</p>
        </div>
      </header>

      <SourceFilters active={activeSources} onToggle={toggleSource} />
      {view.warnings.length > 0 && (
        <div className="warnings" role="status">{view.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>
      )}

      {view.error ? (
        <StatePanel kind="error">{view.error}</StatePanel>
      ) : view.status === "loading" ? (
        <StatePanel kind="loading">正在等待 NOTEBOOK_AI 搜索结果…</StatePanel>
      ) : displayedResults.length === 0 ? (
        <StatePanel kind="empty">当前来源筛选下没有结果。</StatePanel>
      ) : (
        <section className="results" aria-label="检索结果">
          {displayedResults.map((baseResult) => {
            const result = details[baseResult.fragment_id] ?? baseResult;
            return (
              <ResultCard
                key={result.fragment_id}
                result={result}
                selected={selectedIds.includes(result.fragment_id)}
                expanded={expandedIds.has(result.fragment_id)}
                loadingDetail={loadingDetail.has(result.fragment_id)}
                onSelect={() => setSelectedIds((current) => toggleEvidence(current, result.fragment_id))}
                onExpand={() => void toggleExpanded(result)}
                onCopy={() => void exportSelection("markdown", [result.fragment_id])}
                onOpen={(href) => void mcpBridge.openLink(href)}
              />
            );
          })}
        </section>
      )}

      <EvidenceBasket
        selected={selected}
        exporting={exporting}
        status={basketStatus}
        onClear={() => setSelectedIds([])}
        onExport={(format) => void exportSelection(format)}
      />

      {exportPreview && (
        <section className="export-preview">
          <div>
            <h2>导出预览</h2>
            <button type="button" onClick={() => setExportPreview("")}>关闭</button>
          </div>
          <textarea readOnly value={exportPreview} aria-label="导出内容" />
        </section>
      )}
    </main>
  );
}

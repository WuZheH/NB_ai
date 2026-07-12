import { useEffect, useMemo, useState } from "react";
import {
  ApprovedObjectCandidatesSection,
  ResearchEvidencePacketPanel,
  ResearchGateSummary,
  SearchLayerSection,
  StructuredResultSection,
  StructuredRetrievalOverview,
} from "../../features/retrieval/components/RetrievalResultSections.jsx";
import {
  asArray,
  buildEvidencePacketJson,
  buildEvidencePacketText,
  buildPacketQualitySummary,
  copyTextToClipboard,
  downloadTextFile,
  normalizePacketResults,
  normalizeRelatedKeywords,
  packetFilename,
} from "../../features/retrieval/utils/retrievalResults.js";

const LAYERS = [
  {
    key: "passage_results_with_pdf_preview",
    title: "原文片段层",
    emptyCopy: "原文片段层可用，但本章没有直接命中。",
  },
  {
    key: "note_results_with_pdf_preview",
    title: "用户笔记层",
    emptyCopy: "笔记层可用，但本章没有直接命中。",
  },
  {
    key: "object_results",
    title: "对象层",
    gatePrefix: "对象层暂未启用",
  },
  {
    key: "relation_results",
    title: "关系层",
    gatePrefix: "关系层暂未启用",
  },
  {
    key: "insight_or_mechanism_results",
    title: "机制层",
    gatePrefix: "机制层暂未启用",
  },
];

export default function FiveLayerSearchResults({ searchState, onViewSource, onRunRelatedQuery }) {
  const [selectedStableIds, setSelectedStableIds] = useState([]);
  const [copyState, setCopyState] = useState("idle");
  const [jsonCopyState, setJsonCopyState] = useState("idle");
  const payload = searchState.data || {};
  const structured = payload.structured_retrieval_result || payload;
  const researchPacket = payload.research_evidence_packet || structured.research_evidence_packet || {};
  const queryText = payload.query || searchState.query || "";
  const retrievalResults = useMemo(() => {
    return normalizePacketResults(
      researchPacket.retrieval_results
      || structured.retrieval_results
      || payload.retrieval_results
    );
  }, [payload, researchPacket, structured]);
  const selectedStableIdSet = useMemo(() => new Set(selectedStableIds), [selectedStableIds]);
  const selectedResults = useMemo(() => {
    const byStableId = new Map(retrievalResults.map((result) => [result.stable_id, result]));
    return selectedStableIds.map((stableId) => byStableId.get(stableId)).filter(Boolean);
  }, [retrievalResults, selectedStableIds]);
  const selectedQualitySummary = useMemo(() => {
    return buildPacketQualitySummary(selectedResults, researchPacket.quality_summary || structured.quality_summary || payload.quality_summary);
  }, [payload.quality_summary, researchPacket.quality_summary, selectedResults, structured.quality_summary]);
  const relatedKeywords = useMemo(() => {
    return normalizeRelatedKeywords(researchPacket.related_keywords || structured.related_keywords || payload.related_keywords);
  }, [payload.related_keywords, researchPacket.related_keywords, structured.related_keywords]);
  const packetText = useMemo(() => {
    return buildEvidencePacketText(queryText, selectedResults, selectedQualitySummary, relatedKeywords);
  }, [queryText, relatedKeywords, selectedQualitySummary, selectedResults]);
  const packetJson = useMemo(() => {
    return buildEvidencePacketJson(queryText, selectedResults, selectedQualitySummary, relatedKeywords);
  }, [queryText, relatedKeywords, selectedQualitySummary, selectedResults]);
  const packetJsonText = useMemo(() => {
    return selectedResults.length ? JSON.stringify(packetJson, null, 2) : "";
  }, [packetJson, selectedResults.length]);

  useEffect(() => {
    setSelectedStableIds([]);
    setCopyState("idle");
    setJsonCopyState("idle");
  }, [searchState.query, searchState.status]);

  function handleTogglePacketResult(result) {
    const stableId = result?.stable_id;
    if (!stableId) return;
    setCopyState("idle");
    setJsonCopyState("idle");
    setSelectedStableIds((current) => (
      current.includes(stableId)
        ? current.filter((item) => item !== stableId)
        : [...current, stableId]
    ));
  }

  async function handleCopyPacket() {
    if (!packetText || selectedResults.length === 0) return;
    setCopyState("copying");
    try {
      await copyTextToClipboard(packetText);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  async function handleCopyJsonPacket() {
    if (!packetJsonText || selectedResults.length === 0) return;
    setJsonCopyState("copying");
    try {
      await copyTextToClipboard(packetJsonText);
      setJsonCopyState("copied");
    } catch {
      setJsonCopyState("error");
    }
  }

  function handleDownloadMarkdown() {
    if (!packetText || selectedResults.length === 0) return;
    downloadTextFile(packetFilename(queryText, "md"), packetText, "text/markdown;charset=utf-8");
  }

  function handleDownloadJson() {
    if (!packetJsonText || selectedResults.length === 0) return;
    downloadTextFile(packetFilename(queryText, "json"), packetJsonText, "application/json;charset=utf-8");
  }

  if (searchState.status === "idle") {
    return (
      <section className="workspaceSearchResultsShell idle" aria-label="structured retrieval results">
        <div className="workspaceSearchNoQuery">
          <strong>四层搜索结果</strong>
          <span>开始检索后，这里显示原文片段、用户笔记、对象、机制来源的摘要结果。</span>
        </div>
      </section>
    );
  }
  if (searchState.status === "loading") {
    return (
      <section className="workspaceSearchResultsShell loading" aria-label="structured retrieval results">
        <div className="workspaceSearchNoQuery">
          <strong>正在检索结构化研究证据...</strong>
          <span>正在读取本地数据库结果。</span>
        </div>
      </section>
    );
  }
  if (searchState.status === "error") {
    return (
      <section className="workspaceSearchResultsShell error" aria-label="structured retrieval results">
        <div className="workspaceSearchNoQuery warning">
          <strong>工作台检索暂不可用</strong>
          <span>{searchState.error}</span>
        </div>
      </section>
    );
  }

  const layers = payload.layers || {};
  const scopeExpansion = payload.scope_expansion || structured.scope_expansion || {};
  return (
    <section className="workspaceSearchResultsShell" aria-label="structured retrieval results">
      <div className="workspaceSearchResultsHeader">
        <div>
          <p className="workspaceKicker">四层搜索结果</p>
          <h3>Query：{payload.query || searchState.query}</h3>
        </div>
        <WorkspaceStatusPill status="available">只读</WorkspaceStatusPill>
      </div>
      {scopeExpansion.applied && (
        <div className="workspaceSearchLayerWarnings" aria-label="search scope expansion">
          <span>本章未命中主题锚点，已扩展到当前来源全文。</span>
        </div>
      )}

      {asArray(payload.warnings || structured.warnings).length > 0 && (
        <div className="workspaceSearchLayerWarnings" aria-label="search layer warnings">
          {asArray(payload.warnings || structured.warnings).slice(0, 3).map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      )}

      <ResearchEvidencePacketPanel
        qualitySummary={researchPacket.quality_summary || structured.quality_summary || payload.quality_summary}
        retrievalResults={retrievalResults}
        selectedResults={selectedResults}
        selectedQualitySummary={selectedQualitySummary}
        packetText={packetText}
        packetJsonText={packetJsonText}
        relatedKeywords={relatedKeywords}
        copyState={copyState}
        jsonCopyState={jsonCopyState}
        onCopyPacket={handleCopyPacket}
        onCopyJsonPacket={handleCopyJsonPacket}
        onDownloadMarkdown={handleDownloadMarkdown}
        onDownloadJson={handleDownloadJson}
        onRunRelatedQuery={onRunRelatedQuery}
        onClearSelection={() => {
          setSelectedStableIds([]);
          setCopyState("idle");
          setJsonCopyState("idle");
        }}
      />

      <StructuredResultSection
        title="原文片段"
        kicker="PDF 证据"
        results={asArray(structured.evidence_results)}
        emptyCopy="没有直接命中的原文 chunk；PDF passage 层在 chunks 存在时仍可用。"
        onViewSource={onViewSource}
        selectedStableIds={selectedStableIdSet}
        onTogglePacketResult={handleTogglePacketResult}
      />
      <StructuredResultSection
        title="用户笔记"
        kicker="Zotero 笔记"
        results={[...asArray(structured.note_results), ...asArray(structured.inspiration_results)]}
        emptyCopy="没有直接命中的 Zotero 用户笔记，或本章 notes 不可用。"
        onViewSource={onViewSource}
        selectedStableIds={selectedStableIdSet}
        onTogglePacketResult={handleTogglePacketResult}
      />
      <StructuredResultSection
        title="对象"
        kicker="对象 / 候选"
        results={asArray(structured.object_results)}
        emptyCopy="没有正式对象 registry 结果；对象写入保持禁用，只显示已存在候选。"
        onViewSource={onViewSource}
      />
      <ResearchGateSummary
        relationSummary={structured.relation_dry_run_summary || payload.relation_dry_run_summary || {}}
        mechanismSummary={structured.mechanism_readiness_summary || payload.mechanism_readiness_summary || {}}
      />

      <details className="workspaceDisclosure approvedObjectDisclosure" data-disclosure-layout="in-flow">
        <summary>已审核对象详情</summary>
        <ApprovedObjectCandidatesSection candidates={asArray(structured.approved_object_candidates)} />
      </details>

      <details className="workspaceDisclosure structuredDebugDisclosure" data-disclosure-layout="in-flow">
        <summary>检索详情</summary>
        <StructuredRetrievalOverview structured={structured} />
        <section className="workspaceLegacyLayerStatus" aria-label="five layer gate status">
          <div className="workspaceSearchLayerTop">
            <div>
              <h4>五层 gate 状态</h4>
              <p>兼容 passages / notes / objects / relations / mechanisms 的只读状态视图。</p>
            </div>
            <WorkspaceStatusPill status="planned">gate 视图</WorkspaceStatusPill>
          </div>
          {LAYERS.map((layer) => (
            <SearchLayerSection
              key={layer.key}
              layerKey={layer.key}
              config={layer}
              layer={layers[layer.key] || { status: "unavailable", results: [] }}
              onViewSource={onViewSource}
            />
          ))}
        </section>
      </details>
    </section>
  );
}

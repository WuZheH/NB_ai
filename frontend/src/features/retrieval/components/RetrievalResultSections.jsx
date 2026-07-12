import WorkspaceStatusPill from "../../../components/workspace/WorkspaceStatusPill.jsx";
import {
  asArray,
  evidenceLabel,
  gateReasonLabel,
  gateStatusLabel,
  isPacketSelectableResult,
  objectTypeLabel,
  resultCitationFallback,
  reviewActionLabel,
  sourceKindLabel,
  sourceTargetFromResult,
} from "../utils/retrievalResults.js";

export function ResearchEvidencePacketPanel({
  qualitySummary,
  retrievalResults,
  selectedResults,
  selectedQualitySummary,
  packetText,
  packetJsonText,
  relatedKeywords,
  copyState,
  jsonCopyState,
  onCopyPacket,
  onCopyJsonPacket,
  onDownloadMarkdown,
  onDownloadJson,
  onRunRelatedQuery,
  onClearSelection,
}) {
  const risks = asArray(qualitySummary?.risks);
  return (
    <section className="researchEvidencePacketPanel" aria-label="ResearchEvidencePacket-A evidence packet">
      <div className="workspaceSearchLayerTop">
        <div>
          <p className="workspaceKicker">ResearchEvidencePacket-A</p>
          <h4>已选证据包</h4>
        </div>
        <WorkspaceStatusPill status={selectedResults.length ? "available" : "planned"}>
          已选 {selectedResults.length}
        </WorkspaceStatusPill>
      </div>
      <dl className="researchEvidenceQualityGrid" aria-label="recall quality summary">
        <div>
          <dt>PDF chunks</dt>
          <dd>{Number(qualitySummary?.pdf_chunks || 0)}</dd>
        </div>
        <div>
          <dt>User notes</dt>
          <dd>{Number(qualitySummary?.zotero_notes || qualitySummary?.user_notes || 0)}</dd>
        </div>
        <div>
          <dt>Documents</dt>
          <dd>{Number(qualitySummary?.documents || 0)}</dd>
        </div>
        <div>
          <dt>Selected</dt>
          <dd>{selectedResults.length}</dd>
        </div>
      </dl>
      <div className="workspaceSearchLayerWarnings" aria-label="research evidence packet risks">
        {risks.length ? risks.map((risk) => <span key={risk}>{risk}</span>) : <span>recall_quality_no_blocking_risk</span>}
      </div>
      <div className="researchEvidenceRelatedKeywords" aria-label="related keywords">
        <span>Related keywords</span>
        <div>
          {relatedKeywords.length ? relatedKeywords.map((item) => (
            <button
              type="button"
              className="workspacePillButton secondary"
              key={item.keyword}
              onClick={() => onRunRelatedQuery?.(item.keyword)}
              title={`继续搜索：${item.keyword}`}
            >
              {item.keyword}
            </button>
          )) : <em>暂无可用二次检索关键词</em>}
        </div>
      </div>
      {selectedResults.length > 0 ? (
        <div className="researchEvidenceSelectedList" aria-label="selected evidence IDs">
          {selectedResults.map((result) => (
            <span key={result.stable_id}>{result.stable_id}</span>
          ))}
        </div>
      ) : (
        <article className="workspaceSearchGateCard">
          <strong>从原文片段或用户笔记中选择证据。</strong>
          <span>当前可选结果 {retrievalResults.length} 条；只生成本地 evidence packet。</span>
        </article>
      )}
      <div className="researchEvidencePacketActions">
        <button
          type="button"
          className="workspacePillButton"
          disabled={selectedResults.length === 0 || copyState === "copying"}
          onClick={onCopyPacket}
        >
          {copyState === "copied" ? "已复制给 ChatGPT" : copyState === "copying" ? "复制中..." : "复制给 ChatGPT"}
        </button>
        <button
          type="button"
          className="workspacePillButton secondary"
          disabled={selectedResults.length === 0 || jsonCopyState === "copying"}
          onClick={onCopyJsonPacket}
        >
          {jsonCopyState === "copied" ? "已复制 JSON" : jsonCopyState === "copying" ? "复制中..." : "复制 JSON"}
        </button>
        <button
          type="button"
          className="workspacePillButton secondary"
          disabled={selectedResults.length === 0}
          onClick={onDownloadMarkdown}
        >
          下载 Markdown
        </button>
        <button
          type="button"
          className="workspacePillButton secondary"
          disabled={selectedResults.length === 0}
          onClick={onDownloadJson}
        >
          下载 JSON
        </button>
        <button
          type="button"
          className="workspacePillButton secondary"
          disabled={selectedResults.length === 0}
          onClick={onClearSelection}
        >
          清空选择
        </button>
      </div>
      {copyState === "error" && (
        <p className="workspaceSampleNotice warning">浏览器未允许复制，请手动选择 evidence packet 文本。</p>
      )}
      {jsonCopyState === "error" && (
        <p className="workspaceSampleNotice warning">浏览器未允许复制 JSON，请使用下载 JSON。</p>
      )}
      <label className="researchEvidencePacketPreview">
        <span>Evidence packet Markdown</span>
        <textarea
          readOnly
          value={selectedResults.length ? packetText : ""}
          placeholder="选择结果后生成可复制给 ChatGPT 的 evidence packet。"
          spellCheck="false"
        />
      </label>
      <label className="researchEvidencePacketPreview compact">
        <span>Evidence packet JSON</span>
        <textarea
          readOnly
          value={selectedResults.length ? packetJsonText : ""}
          placeholder="选择结果后生成结构化 JSON evidence packet。"
          spellCheck="false"
        />
      </label>
      <code>
        selected_results={selectedQualitySummary.selected_results} · db_write=false · llm_called=false · mechanism_generated=false · relation_generated=false
      </code>
    </section>
  );
}

export function StructuredRetrievalOverview({ structured }) {
  const safety = structured.safety_flags || {};
  return (
    <article className="workspaceStructuredOverview">
      <dl className="workspaceStructuredFacts">
        <div>
          <dt>扩展 query 预览</dt>
          <dd>{structured.expanded_query_preview || "null · SearchExp-A 待进入"}</dd>
        </div>
        <div>
          <dt>原文片段结果</dt>
          <dd>{asArray(structured.evidence_results).length}</dd>
        </div>
        <div>
          <dt>笔记结果</dt>
          <dd>{asArray(structured.note_results).length}</dd>
        </div>
        <div>
          <dt>已审核对象候选</dt>
          <dd>{asArray(structured.approved_object_candidates).length}</dd>
        </div>
      </dl>
      <code>
        DB 写入={String(Boolean(safety.db_write_performed))} · LLM={String(Boolean(safety.llm_called))} · 关系生成={String(Boolean(safety.relation_generated))} · 机制生成={String(Boolean(safety.mechanism_generated))}
      </code>
    </article>
  );
}

export function StructuredResultSection({ title, kicker, results, emptyCopy, onViewSource, selectedStableIds, onTogglePacketResult }) {
  const visibleResults = results.slice(0, 3);
  const expandedResults = results.slice(3);
  return (
    <section className="workspaceStructuredSection" aria-label={title}>
      <div className="workspaceSearchLayerTop">
        <div>
          <p className="workspaceKicker">{kicker}</p>
          <h4>{title}</h4>
        </div>
        <WorkspaceStatusPill status={results.length ? "available" : "planned"}>
          {results.length ? `${results.length} 条` : "空"}
        </WorkspaceStatusPill>
      </div>
      {results.length === 0 && (
        <article className="workspaceSearchGateCard">
          <strong>{emptyCopy}</strong>
          <span>没有可展示的真实数据库命中。</span>
        </article>
      )}
      {visibleResults.map((result, index) => (
        <SearchResultCard
          key={result.id || `${title}-${index}`}
          result={result}
          onViewSource={onViewSource}
          selectedForPacket={selectedStableIds?.has(result.stable_id)}
          onTogglePacketResult={onTogglePacketResult}
        />
      ))}
      {expandedResults.length > 0 && (
        <details className="workspaceDisclosure resultExpandDisclosure" data-disclosure-layout="in-flow">
          <summary>展开全部 {results.length} 条</summary>
          {expandedResults.map((result, index) => (
            <SearchResultCard
              key={result.id || `${title}-expanded-${index}`}
              result={result}
              onViewSource={onViewSource}
              selectedForPacket={selectedStableIds?.has(result.stable_id)}
              onTogglePacketResult={onTogglePacketResult}
            />
          ))}
        </details>
      )}
    </section>
  );
}

export function ApprovedObjectCandidatesSection({ candidates }) {
  return (
    <section className="workspaceStructuredSection" aria-label="approved object candidates">
      <div className="workspaceSearchLayerTop">
        <div>
          <p className="workspaceKicker">已审核对象</p>
          <h4>对象候选（人工审核）</h4>
        </div>
        <WorkspaceStatusPill status={candidates.length ? "reviewed" : "locked"}>
          已审核 {candidates.length}
        </WorkspaceStatusPill>
      </div>
      {candidates.length === 0 && (
        <article className="workspaceSearchGateCard">
          <strong>已审核对象候选暂不可用。</strong>
          <span>此视图不会创建 object registry 行。</span>
        </article>
      )}
      {candidates.slice(0, 3).map((candidate) => (
        <article key={candidate.candidate_temp_id || candidate.id} className="workspaceSearchResultCard object_candidate">
          <div className="workspaceSearchResultMain">
            <div>
              <strong>{candidate.object_name || candidate.candidate_temp_id}</strong>
              <span>{objectTypeLabel(candidate.object_type)} · {reviewActionLabel(candidate.review_action)}</span>
            </div>
            <WorkspaceStatusPill status="reviewed">只读</WorkspaceStatusPill>
          </div>
          <details className="workspaceDisclosure resultMetaDisclosure" data-disclosure-layout="in-flow">
            <summary>高级详情</summary>
            <div className="workspaceSearchResultMeta">
              <span>候选 ID：{candidate.candidate_temp_id || candidate.id}</span>
              <span>来源笔记：{(candidate.source_server_note_ids || []).length}</span>
              <span>chunks：{(candidate.evidence_chunk_ids || []).join(", ") || "n/a"}</span>
              <span>对象 registry 写入=false</span>
            </div>
          </details>
        </article>
      ))}
      {candidates.length > 3 && (
        <p className="workspaceSampleNotice">已折叠其余 {candidates.length - 3} 个对象候选。</p>
      )}
    </section>
  );
}

export function ResearchGateSummary({ relationSummary, mechanismSummary }) {
  return (
    <section className="workspaceStructuredGateGrid" aria-label="relation and mechanism readiness">
      <article className="workspaceSearchGateCard">
        <strong>机制来源：关系候选 {Number(relationSummary.candidate_count || 0)} 条</strong>
        <span>Phase7H 未进入 · PN68 已排除</span>
      </article>
      <article className="workspaceSearchGateCard">
        <strong>机制 readiness：{gateStatusLabel(mechanismSummary.status || "locked")}</strong>
        <span>{gateReasonLabel(mechanismSummary.reason || "relations_not_reviewed_phase7h")} · 机制生成未启用。</span>
      </article>
    </section>
  );
}

export function SearchLayerSection({ layerKey, config, layer, onViewSource }) {
  const results = layer.results || [];
  const gated = layer.status === "locked" || layer.status === "planned" || layer.status === "unavailable";
  return (
    <section className={`workspaceSearchLayerSection ${layer.status || "unavailable"}`} aria-label={layerKey}>
      <div className="workspaceSearchLayerTop">
        <div>
          <h4>{config.title}</h4>
          <p>{gateReasonLabel(layer.reason || layer.status || "unavailable")}</p>
        </div>
        <WorkspaceStatusPill status={layer.status || "unavailable"} />
      </div>

      {gated && results.length === 0 && (
        <SearchEmptyGateCard
          prefix={config.gatePrefix}
          reason={layer.reason}
          noDirectMatch={layer.no_direct_match}
          emptyCopy={config.emptyCopy}
          status={layer.status}
        />
      )}

      {!gated && results.length === 0 && (
        <SearchEmptyGateCard
          reason={layer.reason}
          noDirectMatch={layer.no_direct_match}
          emptyCopy={config.emptyCopy}
          status={layer.status}
        />
      )}

      {results.map((result) => (
        <SearchResultCard
          key={result.id}
          result={result}
          onViewSource={onViewSource}
        />
      ))}
    </section>
  );
}

export function SearchResultCard({ result, onViewSource, selectedForPacket = false, onTogglePacketResult }) {
  const target = sourceTargetFromResult(result);
  const sourceType = result.source_type || result.source_kind;
  const packetSelectable = isPacketSelectableResult(result);
  return (
    <article className={`workspaceSearchResultCard ${result.source_kind || result.source_type || ""}`} data-card-layout="flow">
      <div className="workspaceSearchResultMain">
        <div className="workspaceSearchResultHeaderText">
          <strong>{result.title || "未命名结果"}</strong>
          <span className="workspaceSearchCitationLabel">{result.citation_label || resultCitationFallback(result)}</span>
          {result.stable_id && <span className="workspaceSearchStableId">{result.stable_id}</span>}
        </div>
        <div className="workspaceSearchResultChips" aria-label="result layer and review status">
          <span className="workspaceSearchTypeChip">{sourceKindLabel(sourceType)}</span>
          {result.score !== undefined && <span className="workspaceSearchTypeChip">score {result.score}</span>}
          {result.review_badge && <WorkspaceStatusPill status="raw_unreviewed">{result.review_badge}</WorkspaceStatusPill>}
        </div>
      </div>
      <p className="workspaceSearchSnippet">{result.snippet || result.selected_text || result.note_text || "暂无片段。"}</p>
      <div className="workspaceSearchResultActions">
        {target ? (
          <button type="button" className="workspacePillButton" onClick={() => onViewSource?.(target)}>
            定位到 PDF
          </button>
        ) : (
          <span className="workspaceLocatorWarning">无法定位，但保留文本证据</span>
        )}
        {packetSelectable && (
          <button
            type="button"
            className={`workspacePillButton ${selectedForPacket ? "selected" : ""}`}
            onClick={() => onTogglePacketResult?.(result)}
          >
            {selectedForPacket ? "移出证据包" : "加入证据包"}
          </button>
        )}
      </div>
      <details className="workspaceDisclosure resultMetaDisclosure" data-disclosure-layout="in-flow">
        <summary>高级详情</summary>
        <div className="workspaceSearchResultMeta">
          {result.stable_id && <span>stable_id: {result.stable_id}</span>}
          {result.citation_token && <span>citation_token: {result.citation_token}</span>}
          {asArray(result.citation_tokens).length > 0 && <span>citation_tokens: {result.citation_tokens.join(", ")}</span>}
          {result.source_locator && <span>source_locator: {JSON.stringify(result.source_locator)}</span>}
          <span>来源类型：{sourceKindLabel(result.source_type || result.source_kind)}</span>
          {result.chunk_id && <span>chunk_id: {result.chunk_id}</span>}
          {result.note_id && <span>note_id: {result.note_id}</span>}
          {result.server_note_id && <span>server_note_id: {result.server_note_id}</span>}
          {result.zotero_annotation_key && <span>zotero_annotation_key: {result.zotero_annotation_key}</span>}
          {result.heading_path && <span>heading_path: {result.heading_path}</span>}
        </div>
        <ResultEvidenceFields result={result} />
      </details>
    </article>
  );
}

export function ResultEvidenceFields({ result }) {
  const fields = [
    ["note_text", result.note_text],
    ["selected_text", result.selected_text],
    ["chunk_evidence_text", result.chunk_evidence_text],
  ].filter(([, value]) => value);
  if (!fields.length) return null;
  return (
    <div className="workspaceSearchEvidenceFields">
      {fields.map(([label, value]) => (
        <section key={label}>
          <span>{evidenceLabel(label)}</span>
          <p>{value}</p>
        </section>
      ))}
    </div>
  );
}

export function SearchEmptyGateCard({ prefix, reason, noDirectMatch, emptyCopy, status }) {
  const copy = prefix
    ? `${prefix} · ${gateReasonLabel(reason || status || "locked")}`
    : noDirectMatch
      ? emptyCopy
    : gateReasonLabel(reason) || emptyCopy || "本章该层不可用。";
  return (
    <article className="workspaceSearchGateCard">
      <strong>{copy}</strong>
      <span>没有可展示的真实数据库命中。</span>
    </article>
  );
}

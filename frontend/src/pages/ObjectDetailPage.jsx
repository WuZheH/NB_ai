import { useState } from "react";
import { API_BASE_URL } from "../api/client.js";
import PdfLocationPreview, { buildDocumentPdfPath, extractPage } from "../PdfLocationPreview.jsx";
import StateMessage from "../components/StateMessage.jsx";
import TagList from "../components/TagList.jsx";
import FourLayerTags from "../components/FourLayerTags.jsx";
import LocatorSummary from "../components/LocatorSummary.jsx";
import PdfActionGroup from "../components/PdfActionGroup.jsx";
import { enhanceSourceWithZoteroCandidate } from "../utils/formatters.js";

export default function ObjectDetailPage({ state, locatorState, zoteroCandidateState, onBack, onOpenEvidence, onLocateEvidence }) {
  const [activePreviewChunkId, setActivePreviewChunkId] = useState(null);

  if (state.status === "loading") return <StateMessage title="正在加载对象详情" />;
  if (state.status === "error") return <StateMessage title="对象详情暂不可用" body={state.error} />;
  if (!state.data?.object) return <StateMessage title="未找到对象详情" body={state.error || state.data?.message} />;

  const object = state.data.object;
  const evidenceRefs = Array.isArray(object.evidence_refs) ? object.evidence_refs : [];
  const mappedChunkIds = normalizeChunkIds(object.mapped_chunk_ids ?? object.mapped_chunk_ids_json);
  const mappedChunks = buildMappedChunks(evidenceRefs, mappedChunkIds, object);
  const warnings = normalizeWarnings(object.mapping_warnings ?? object.warnings_json ?? object.warnings);
  const activeChunk = mappedChunks.find((chunk) => chunk.chunk_id === activePreviewChunkId);
  const activeLocator = activePreviewChunkId ? locatorState?.byChunkId?.[activePreviewChunkId] : null;
  const activeDocumentId = activeChunk?.document_id || object.document_id;
  const activeSourceUrl = activeChunk?.preferred_source_open_url || activeChunk?.pdf_fallback_url || "";
  const activeCanLocate = activeChunk?.is_locatable === true;
  const primaryEvidence = mappedChunks[0] || evidenceRefs[0] || {};
  const sourceChain = buildSourceChain(object, primaryEvidence);

  return (
    <section className="detailStack">
      <button className="detailBack" type="button" onClick={onBack}>
        返回
      </button>
      <article className="objectHero">
        <div className="objectHeroTitle">
          <div className="objectHeroPills">
            <span>{objectTypeLabel(object.object_type_label || object.object_type)}</span>
            {object.review_status === "accepted" && <span>已审核</span>}
            {object.review_status === "edited" && <span>已编辑</span>}
          </div>
          <h3>{object.object_name}</h3>
        </div>
        <div className="objectDocumentLine">
          <span>来源链路</span>
          <strong>{sourceChain || "来源暂不可用"}</strong>
        </div>
      </article>

      <section className="objectDetailSection">
        <div className="sectionHeader">
          <h3>对象解释</h3>
        </div>
        {object.description ? (
          <p className="objectDescription">{object.description}</p>
        ) : (
          <p className="subtlePlaceholder">暂无对象说明。</p>
        )}
        <div className="objectAliasBlock">
          <span>别名 / Aliases</span>
          <TagList tags={object.aliases || []} />
        </div>
        <ReasonBlock object={object} />
      </section>

      <section className="objectDetailSection">
        <div className="sectionHeader">
          <h3>四层标签</h3>
        </div>
        <FourLayerTags object={object} />
      </section>

      <section>
        <div className="sectionHeader">
          <h3>证据支持</h3>
          <span>{mappedChunks.length} 条支撑片段</span>
        </div>
        {!mappedChunks.length ? (
          <StateMessage title="暂无可定位证据" body="当前对象没有 mapped_chunk_ids，或映射结果尚未返回可定位 chunk。" />
        ) : (
          <div className="mappedChunkList">
            {mappedChunks.map((rawEvidence) => {
              const evidence = enhanceSourceWithZoteroCandidate(
                rawEvidence,
                zoteroCandidateState?.byDocumentId?.[rawEvidence.document_id]
              );
              return (
              <article key={evidence.chunk_id} className={`mappedChunkCard mappedChunkCard--${evidence.mapping_status || "unknown"}`}>
                <div className="cardMeta">
                  <span>chunk {evidence.chunk_id}</span>
                  {pageLabel(evidence) && <span>{pageLabel(evidence)}</span>}
                  {evidence.mapping_status && <span className={`mappingPill mappingPill--${evidence.mapping_status}`}>{evidence.mapping_status}</span>}
                </div>
                <h3>{evidence.document_title || object.document_title || object.top_documents?.[0]?.title || "Untitled document"}</h3>
                <div className="chunkSectionPath">
                  <span>位置</span>
                  <strong>{evidence.heading_path || evidence.section_label || "未识别章节"}</strong>
                </div>
                <p className="summaryClamp">{evidence.chunk_text || evidence.snippet || evidence.quote_text_short || "暂无片段文本。"}</p>
                <ChunkWarning evidence={evidence} warnings={warnings} />
                <div className="mappedChunkActions">
                  <button type="button" className="quietButton" onClick={() => onOpenEvidence(evidence.chunk_id, evidence)}>
                    查看详情
                  </button>
                  {evidence.is_locatable ? (
                    <PdfActionGroup
                      source={{ ...evidence, locator_result: locatorState?.byChunkId?.[evidence.chunk_id]?.payload?.location }}
                      showPreview
                      onPreview={() => {
                        setActivePreviewChunkId(evidence.chunk_id);
                        onLocateEvidence?.(evidence.chunk_id, evidence);
                      }}
                    />
                  ) : (
                    <PdfActionGroup source={evidence} />
                  )}
                </div>
              </article>
            );
            })}
          </div>
        )}
        {activeChunk && activeCanLocate && (
          <div className="objectPreviewSlot">
            <LocatorSummary state={activeLocator} />
            <PdfLocationPreview
              apiBase={API_BASE_URL}
              documentId={activeDocumentId}
              location={activeLocator?.payload?.location}
              pdfUrl={buildDocumentPdfPath(activeDocumentId)}
              page={activeLocator?.payload?.location?.pdf_page ?? extractPage(activeSourceUrl) ?? activeChunk.pdf_page_start ?? activeChunk.pdf_page}
              pdf_page_start={activeChunk.pdf_page_start}
              pdf_page_end={activeChunk.pdf_page_end}
              chunkId={activeChunk.chunk_id}
              highlightText={activeChunk.chunk_text || activeChunk.snippet || activeChunk.quote_text_short}
            />
          </div>
        )}
      </section>

      <details className="objectRawEvidence">
        <summary>原始引用 ({evidenceRefs.length})</summary>
        <div className="rawEvidenceList">
          {evidenceRefs.length ? evidenceRefs.map((ref, index) => (
            <article key={`${ref.chunk_id || "raw"}-${index}`} className="rawEvidenceItem">
              <div className="cardMeta">
                {ref.pdf_page && <span>p. {ref.pdf_page}</span>}
                {ref.pdf_page_start && <span>p. {ref.pdf_page_start}</span>}
                {ref.chunk_id && <span>chunk {ref.chunk_id}</span>}
              </div>
              <strong>{ref.section_title || ref.heading_path || ref.section_label || "未标注章节"}</strong>
              <p>{ref.quote_text_short || ref.snippet || "暂无原始引用文本。"}</p>
            </article>
          )) : <p className="subtlePlaceholder">暂无原始 evidence_refs。</p>}
        </div>
      </details>

      <section>
        <div className="sectionHeader">
          <h3>关联笔记</h3>
          <span>{object.linked_personal_notes?.length || 0} 条笔记</span>
        </div>
        {(object.linked_personal_notes || []).length === 0 ? (
          <StateMessage title="暂无直接关联个人笔记" />
        ) : (
          <div className="resultList">
            {object.linked_personal_notes.map((note) => (
              <article key={`${note.note_id}-${note.linked_chunk_id}`} className="resultItem">
                <div className="cardMeta">
                  <span>{note.note_type}</span>
                  <span>chunk {note.linked_chunk_id}</span>
                </div>
                <h3>{note.title}</h3>
                <p>{note.short_preview}</p>
              </article>
            ))}
          </div>
        )}
      </section>
      {!!warnings.length && (
        <section className="objectDetailSection">
          <div className="sectionHeader">
            <h3>映射提示</h3>
            <span>{warnings.length}</span>
          </div>
          <div className="warningList">
            {warnings.map((warning, index) => (
              <span key={`${warning}-${index}`}>{formatWarning(warning)}</span>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}

function ReasonBlock({ object }) {
  const reasons = Array.isArray(object.match_summary)
    ? object.match_summary
    : Array.isArray(object.reasons)
      ? object.reasons
      : [];
  if (!reasons.length) return <p className="subtlePlaceholder">暂无匹配摘要。</p>;
  return (
    <div className="reasonBlock">
      {reasons.map((reason, index) => (
        <span key={`${reason}-${index}`}>{reason}</span>
      ))}
    </div>
  );
}

function ChunkWarning({ evidence, warnings }) {
  const mappingStatus = evidence.mapping_status;
  const warningText = evidence.warning || evidence.mapping_warning;
  if (mappingStatus === "failed") return <div className="chunkWarning danger">映射失败：{warningText || "未找到可用 chunk。"}</div>;
  if (mappingStatus === "partial") return <div className="chunkWarning">部分映射：{warningText || "证据只匹配到部分 chunk。"}</div>;
  if (warningText) return <div className="chunkWarning">{warningText}</div>;
  if (warnings.some((warning) => String(warning).includes("partial"))) return <div className="chunkWarning">存在 partial mapping warning，请查看 Warnings 区。</div>;
  return null;
}

function buildMappedChunks(evidenceRefs, mappedChunkIds, object) {
  const byChunk = new Map();
  evidenceRefs.forEach((ref) => {
    const chunkId = Number(ref.chunk_id);
    if (Number.isFinite(chunkId)) {
      byChunk.set(chunkId, normalizeEvidence(ref, object));
    }
  });
  mappedChunkIds.forEach((chunkId) => {
    if (!byChunk.has(chunkId)) {
      byChunk.set(chunkId, normalizeEvidence({ chunk_id: chunkId }, object));
    }
  });
  return Array.from(byChunk.values());
}

function normalizeEvidence(ref, object) {
  const pdfPage = ref.pdf_page_start ?? ref.pdf_page;
  const chunkText = ref.chunk_text || ref.snippet || ref.quote_text_short || "";
  const locatorStatus = ref.locator_status || locatorStatusFor(ref, chunkText, pdfPage);
  const documentId = ref.document_id || object.document_id;
  const fallbackLocatable = Boolean(documentId && pdfPage && chunkText && locatorStatus !== "metadata_non_locatable");
  return {
    ...ref,
    document_id: documentId,
    mapping_status: ref.mapping_status || object.mapping_status,
    pdf_page_start: pdfPage,
    pdf_page_end: ref.pdf_page_end ?? ref.pdf_page,
    heading_path: ref.heading_path || ref.section_label || ref.section_title || "",
    chunk_text: chunkText,
    is_metadata_chunk: ref.is_metadata_chunk === true || locatorStatus === "metadata_non_locatable",
    is_locatable: ref.is_locatable === true || fallbackLocatable,
    locator_status: locatorStatus,
    locator_reason: ref.locator_reason || locatorReasonFor(locatorStatus)
  };
}

function normalizeChunkIds(value) {
  if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite);
  if (typeof value === "string" && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.map(Number).filter(Number.isFinite) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function normalizeWarnings(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === "string" && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [value];
    } catch {
      return [value];
    }
  }
  return [];
}

function pageLabel(evidence) {
  const start = evidence.pdf_page_start ?? evidence.pdf_page;
  const end = evidence.pdf_page_end;
  if (!start) return "";
  if (end && end !== start) return `p. ${start}-${end}`;
  return `p. ${start}`;
}

function buildSourceChain(object, evidence = {}) {
  const title = object.document_title || object.top_documents?.[0]?.title || evidence.document_title;
  let heading = evidence.heading_path || evidence.section_title || evidence.section_label || object.heading_path || object.section_title;
  if (title && typeof heading === "string" && heading.startsWith(`${title} / `)) {
    heading = heading.slice(title.length + 3);
  }
  const chunkId = evidence.chunk_id || object.evidence_refs?.[0]?.chunk_id;
  const page = evidence.pdf_page_start ?? evidence.pdf_page ?? object.evidence_refs?.[0]?.pdf_page_start ?? object.evidence_refs?.[0]?.pdf_page;
  const parts = [];
  if (title) parts.push(title);
  if (heading && heading !== title) parts.push(heading);
  if (chunkId) parts.push(`chunk ${chunkId}`);
  if (page) parts.push(`p.${page}`);
  return Array.from(new Set(parts)).join(" / ");
}

function objectTypeLabel(value) {
  const labels = {
    method: "方法",
    dataset: "数据集",
    metric: "指标",
    problem: "问题",
    mechanism: "机制",
    task: "任务",
    concept: "概念",
    contribution: "贡献",
    limitation: "限制",
    inspiration: "灵感",
    experiment: "实验设置",
    experiment_setting: "实验设置",
    loss: "损失函数",
    other: "其他"
  };
  return labels[String(value || "").toLowerCase()] || value || "对象";
}

function formatWarning(warning) {
  if (typeof warning === "string") return warning;
  return warning?.warning || warning?.message || JSON.stringify(warning);
}

function locatorStatusFor(ref, chunkText, pdfPage) {
  const text = String(chunkText || "").trim();
  const lower = text.toLowerCase();
  if (lower.startsWith("- backend:") || lower.startsWith("backend:")) return "metadata_non_locatable";
  if (!pdfPage) return "no_page";
  if (!text) return "no_text";
  return ref.document_id ? "page_level_only" : "pdf_missing";
}

function locatorReasonFor(status) {
  const reasons = {
    metadata_non_locatable: "该片段是抽取元信息，不支持 PDF 定位",
    no_page: "该片段缺少 PDF 页码，只能打开文档",
    no_text: "该片段缺少正文文本，无法定位",
    pdf_missing: "该片段缺少可预览 PDF",
    page_level_only: "可打开 PDF 页码，未保证精确高亮",
    not_found: "未能定位该片段"
  };
  return reasons[status] || "该片段暂不支持 PDF 定位";
}

import { API_BASE_URL } from "../../api/client.js";
import PdfLocationPreview, { buildDocumentPdfPath } from "../../PdfLocationPreview.jsx";
import PdfHighlightOverlay from "./PdfHighlightOverlay.jsx";

export default function PdfPagePreview({ sourceTarget, locatorState }) {
  if (!sourceTarget?.documentId) {
    return (
      <section className="workspacePdfViewportCard">
        <strong>PDF 预览暂不可用</strong>
        <span>当前 source target 没有关联 document id。</span>
      </section>
    );
  }
  const location = locatorState?.payload?.location || null;
  const page = location?.pdf_page || sourceTarget.page || null;
  const heroSnippet = buildHeroSnippet(sourceTarget, location);
  const heroLines = buildHeroLines(heroSnippet);
  const heroStatus = buildHeroStatus(sourceTarget, location);
  return (
    <section className="workspacePdfViewportCard" aria-label="PDF 预览与证据定位">
      <div className="workspacePdfViewportHeader">
        <div>
          <strong>整本 PDF 预览</strong>
          <span>
            {sourceKindLabel(sourceTarget.sourceKind) || "来源"} · {sourceTarget.pageLabel || (page ? `p.${page}` : "页码不可用")}
            {sourceTarget.matchedChunkId ? ` · chunk ${sourceTarget.matchedChunkId}` : ""}
          </span>
        </div>
        <code>PDF.js · 整本 PDF 滚动预览 · bbox/page fallback · 证据定位</code>
      </div>
      <PdfLocatorStatus sourceTarget={sourceTarget} location={location} />
      <section className="workspacePdfHeroShell" aria-label="PDF 页面证据预览">
        <div className="workspacePdfHeroMeta">
          <span className="workspacePdfPageChip">{page ? `p.${page}` : "p.--"}</span>
          <span className="workspacePdfHeroLabel">
            {sourceKindLabel(sourceTarget.sourceKind) || "章节"} · {sourceTarget.pageLabel || (page ? `p.${page}` : "页码不可用")}
          </span>
        </div>
        <div className="workspacePdfPaperPreview" data-preview-mode={heroSnippet ? "snippet" : "skeleton"}>
          <div className="workspacePdfPaperColumn">
            <div className="workspacePdfPaperEyebrow">
              <strong>{sourceTarget.chunkHeadingPath || "证据定位"}</strong>
              <span>{heroStatus}</span>
            </div>
            {heroLines.map((line, index) => (
              line.kind === "text" ? (
                <p key={`hero-line-${index}`} className="workspacePdfPaperLine text">
                  {line.text}
                </p>
              ) : (
                <span
                  key={`hero-line-${index}`}
                  className={`workspacePdfPaperLine ${line.width}`}
                  aria-hidden="true"
                />
              )
            ))}
          </div>
          <span className={`workspacePdfLocatorMarker ${locationMarkerTone(location, sourceTarget)}`} aria-hidden="true" />
        </div>
      </section>
      <PdfLocationPreview
        apiBase={API_BASE_URL}
        documentId={sourceTarget.documentId}
        location={location || fallbackPageLocation(sourceTarget)}
        pdfUrl={buildDocumentPdfPath(sourceTarget.documentId)}
        page={page}
        chunkId={sourceTarget.matchedChunkId}
        quote={sourceTarget.selectedText || sourceTarget.chunkEvidenceText}
        highlightText={sourceTarget.chunkEvidenceText || sourceTarget.selectedText}
      />
      <PdfHighlightOverlay sourceTarget={sourceTarget} locatorLocation={location} />
    </section>
  );
}

function PdfLocatorStatus({ sourceTarget, location }) {
  const bbox = normalizeBbox(sourceTarget?.bbox);
  const hasBbox = Boolean(location?.rects?.length || bbox?.rects?.length);
  const hasChunk = Boolean(sourceTarget?.matchedChunkId);
  const hasPage = Boolean(location?.pdf_page || sourceTarget?.page);
  const status = hasBbox
    ? "bbox / 高亮可用"
    : hasChunk
      ? "chunk 定位 fallback"
      : hasPage
        ? "page fallback"
        : "仅文本证据";
  return (
    <div className={`pdfHighlightStatus ${hasBbox ? "mapped" : hasPage ? "fallback" : "pending"}`}>
      <span>定位状态：{status}</span>
      <span>{hasPage ? "PDF 跳转可用" : "无法定位，但保留文本证据"}</span>
    </div>
  );
}

function buildHeroSnippet(sourceTarget, location) {
  return cleanHeroText(
    sourceTarget.chunkEvidenceText
      || sourceTarget.selectedText
      || sourceTarget.noteText
      || location?.snippet_used
      || ""
  );
}

function buildHeroStatus(sourceTarget, location) {
  if (location?.locator_status === "layout_bbox_location" || location?.locator_status === "layout_block_location") {
    return "版面高光可用";
  }
  if (sourceTarget?.matchedChunkId) return "定位线已就绪";
  if (sourceTarget?.page) return "page fallback";
  return "文本证据预览";
}

function buildHeroLines(snippet) {
  if (snippet) {
    return [
      { kind: "text", text: snippet },
      { kind: "skeleton", width: "long" },
      { kind: "skeleton", width: "medium" },
      { kind: "skeleton", width: "long" },
      { kind: "skeleton", width: "short" },
    ];
  }
  return [
    { kind: "skeleton", width: "long" },
    { kind: "skeleton", width: "medium" },
    { kind: "skeleton", width: "long" },
    { kind: "skeleton", width: "medium" },
    { kind: "skeleton", width: "short" },
    { kind: "skeleton", width: "long" },
  ];
}

function locationMarkerTone(location, sourceTarget) {
  if (location?.rects?.length || sourceTarget?.bbox?.rects?.length) return "mapped";
  if (sourceTarget?.matchedChunkId || sourceTarget?.page) return "fallback";
  return "pending";
}

function fallbackPageLocation(sourceTarget) {
  const bbox = normalizeBbox(sourceTarget?.bbox);
  const rects = bbox?.rects || [];
  const page = bbox?.pdf_page || sourceTarget.page;
  return {
    status: rects.length ? "located" : "page_level_only",
    locator_status: rects.length ? "layout_bbox_location" : "page_level_only",
    locator_reason: rects.length
      ? "使用结构化检索 source target 提供的 bbox。"
      : "未找到 matched chunk 定位结果，显示来源页面和文本证据。",
    is_locatable: Boolean(page),
    document_id: sourceTarget.documentId,
    chunk_id: sourceTarget.matchedChunkId,
    pdf_page: page,
    page_label: bbox?.page_label || sourceTarget.pageLabel || "",
    rects,
    highlight_count: rects.length,
    visual_mode: rects.length ? "layout_block_highlight" : "none",
  };
}

function sourceKindLabel(sourceKind) {
  if (sourceKind === "note") return "笔记";
  if (sourceKind === "passage") return "原文片段";
  if (sourceKind === "chapter") return "章节";
  if (sourceKind === "object_evidence") return "对象证据";
  if (sourceKind === "relation_evidence") return "关系证据";
  if (sourceKind === "mechanism_evidence") return "机制来源";
  return sourceKind || "";
}

function normalizeBbox(value) {
  if (!value) return null;
  if (typeof value === "string") {
    try {
      return normalizeBbox(JSON.parse(value));
    } catch {
      return null;
    }
  }
  if (Array.isArray(value)) {
    return { rects: value };
  }
  if (Array.isArray(value.rects)) {
    return {
      rects: value.rects,
      pdf_page: value.pdf_page || value.page || null,
      page_label: value.page_label || value.pageLabel || "",
    };
  }
  return null;
}

function cleanHeroText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 220);
}

import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

export default function PdfHighlightOverlay({ sourceTarget, locatorLocation }) {
  const rectCount = locatorLocation?.highlight_count ?? locatorLocation?.rects?.length ?? 0;
  const bboxRectCount = sourceTarget?.bbox?.rects?.length ?? 0;
  const hasMappedOverlay = rectCount > 0 && locatorLocation?.is_locatable !== false;
  const hasSourceBbox = Boolean(sourceTarget?.bbox);

  if (hasMappedOverlay) {
    return (
      <div className="pdfHighlightStatus mapped" aria-label="bbox highlight status">
        <WorkspaceStatusPill status="available">高亮 overlay</WorkspaceStatusPill>
        <span>PDF.js overlay 已启用 · {rectCount} 个 rect 来自 pdf-location</span>
      </div>
    );
  }

  if (hasSourceBbox && bboxRectCount > 0) {
    return (
      <div className="pdfHighlightStatus pending" aria-label="bbox highlight status">
        <WorkspaceStatusPill status="planned">bbox 待映射</WorkspaceStatusPill>
        <span>bbox 可用，但 overlay 尚未映射</span>
      </div>
    );
  }

  return (
    <div className="pdfHighlightStatus fallback" aria-label="bbox highlight status">
      <WorkspaceStatusPill status="planned">文本 fallback</WorkspaceStatusPill>
      <span>没有 bbox，显示文本证据 fallback</span>
    </div>
  );
}

import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

export default function SourceEvidenceCard({ sourceTarget }) {
  if (!sourceTarget) return null;
  return (
    <article className="sourceEvidenceCard" aria-label="source evidence text">
      <div className="sourceEvidenceHeader">
        <div>
          <strong>{sourceTitle(sourceTarget)}</strong>
          <span>
            {sourceTarget.pageLabel || (sourceTarget.page ? `p.${sourceTarget.page}` : "页码不可用")}
          </span>
        </div>
        <WorkspaceStatusPill status={sourceTarget.sourceKind === "note" ? "raw_unreviewed" : "available"}>
          {sourceKindLabel(sourceTarget.sourceKind)}
        </WorkspaceStatusPill>
      </div>

      {sourceTarget.selectedText && (
        <section className="sourceEvidenceTextBlock selectedTextBlock" data-field="selected_text">
          <span>选中文本</span>
          <mark>{sourceTarget.selectedText}</mark>
        </section>
      )}
      {sourceTarget.noteText && (
        <section className="sourceEvidenceTextBlock" data-field="note_text">
          <span>笔记</span>
          <p>{sourceTarget.noteText}</p>
        </section>
      )}
      {sourceTarget.chunkEvidenceText && (
        <section className="sourceEvidenceTextBlock" data-field="chunk_evidence_text">
          <span>原文片段</span>
          <p>{sourceTarget.chunkEvidenceText}</p>
        </section>
      )}
      {!sourceTarget.selectedText && !sourceTarget.noteText && !sourceTarget.chunkEvidenceText && (
        <p className="sourceEvidenceEmpty">没有附加文本证据，仅显示 PDF 页面。</p>
      )}
    </article>
  );
}

function sourceTitle(sourceTarget) {
  if (sourceTarget.sourceKind === "note") return "Zotero 笔记来源";
  if (sourceTarget.sourceKind === "passage") return "原文片段来源";
  if (sourceTarget.sourceKind === "object_evidence") return "对象来源";
  if (sourceTarget.sourceKind === "relation_evidence") return "关系来源";
  if (sourceTarget.sourceKind === "mechanism_evidence") return "机制来源";
  return "章节来源";
}

function sourceKindLabel(sourceKind) {
  if (sourceKind === "note") return "笔记";
  if (sourceKind === "passage") return "原文片段";
  if (sourceKind === "chapter") return "章节";
  if (sourceKind === "object_evidence") return "对象来源";
  if (sourceKind === "relation_evidence") return "关系来源";
  if (sourceKind === "mechanism_evidence") return "机制来源";
  return sourceKind || "来源";
}

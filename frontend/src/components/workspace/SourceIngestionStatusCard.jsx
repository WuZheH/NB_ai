import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

export default function SourceIngestionStatusCard({ state }) {
  const document = state?.document || {};
  const chapter = state?.current_chapter || {};
  const source = state?.source_ingestion_status || {};
  const notes = state?.notes_import_status || {};
  const chapterPages = chapter.page_start
    ? `p.${chapter.page_start}-${chapter.page_end || chapter.page_start}`
    : "页码范围不可用";

  return (
    <div className="sourcePdfPanel workspacePanelStack" aria-label="source pdf summary">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">来源</p>
          <h3>来源</h3>
        </div>
        <WorkspaceStatusPill status={source.pdf_available ? "available" : "unavailable"}>
          {source.pdf_available ? "可定位" : "待恢复"}
        </WorkspaceStatusPill>
      </div>

      <article className="workspaceSourceCard primarySource">
        <div className="workspaceSourceIcon" aria-hidden="true">PDF</div>
        <div>
          <h4>PDF 证据</h4>
          <strong>{document.title || "未命名文档"}</strong>
          <p>{chapter.title || "未选择章节"}</p>
          <small>{chapterPages} · 证据定位优先显示 PDF 页面</small>
          <div className="workspaceSourceActions" aria-label="source panel compact actions">
            <span>+ 添加来源</span>
            <span>来源详情</span>
          </div>
        </div>
      </article>

      <div className="sourceSelectionHint">
        <strong>搜索或点击图谱节点后，左栏会定位到对应 PDF 证据。</strong>
        <span>默认显示当前章节的 PDF 页面；有 bbox 时叠加高亮，没有 bbox 时使用页码和文本 fallback。</span>
      </div>

      <details className="workspaceDisclosure sourceDetailsDisclosure">
        <summary>来源详情</summary>
        <section className="workspacePipelineFacts" aria-label="source ingestion status">
          <span>PDF 来源：{source.pdf_available ? "可用" : "不可用"}</span>
          <span>原文片段：{source.chunked ? `${Number(source.chunk_count || 0)} 条` : "尚未切分"}</span>
          <span>Zotero 来源：{source.zotero_source_available ? "可用" : "未配置"}</span>
          <span>关联笔记：{Number(notes.existing || 0)} 条</span>
          <span>用户笔记：{Number(notes.user_notes || 0)} 条</span>
          <span>仅证据笔记：{Number(notes.evidence_only || 0)} 条</span>
        </section>
      </details>
    </div>
  );
}

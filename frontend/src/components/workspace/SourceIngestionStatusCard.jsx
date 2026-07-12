import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import WorkspaceWorkflowLink from "./WorkspaceWorkflowLink.jsx";
import { normalizeWorkspaceState } from "../../utils/workspaceStateAdapter.js";

export default function SourceIngestionStatusCard({ state, onOpenAdvancedWorkflow }) {
  const document = state?.document || {};
  const chapter = state?.current_chapter || {};
  const display = normalizeWorkspaceState(state);
  const { source, noNotes, saveBlocked, sourceDisplay } = display;
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
          <span>{sourceDisplay.pdfStatusLine}</span>
          <span>{sourceDisplay.chunksLine}</span>
          <span>{sourceDisplay.zoteroNotesLine}</span>
          {!noNotes && <span>{sourceDisplay.userNotesLine}</span>}
          {!noNotes && <span>{sourceDisplay.evidenceOnlyLine}</span>}
          <span>{sourceDisplay.importStatusLine}</span>
          <span>{sourceDisplay.notesLayerLine}</span>
          <span>{sourceDisplay.correctionLine}</span>
        </section>

        <article className={`workspaceGateCard ${noNotes ? "locked" : ""}`} aria-label="Zotero notes status">
          <div className="workspaceGateTop">
            <strong>{sourceDisplay.zoteroNotesLine}</strong>
            <WorkspaceStatusPill status={saveBlocked ? "blocked" : noNotes ? "locked" : "available"}>
              {pipelineStatusLabel(display.pipelineStatus)}
            </WorkspaceStatusPill>
          </div>
          {noNotes ? (
            <p>笔记层不可用 · 纠错审核未启用</p>
          ) : (
            <p>
              {sourceDisplay.userNotesLine} · {sourceDisplay.evidenceOnlyLine} · {sourceDisplay.correctionLine}
            </p>
          )}
        </article>

        {!noNotes && (
          <article className={`workspaceGateCard ${saveBlocked ? "blocked" : ""}`} aria-label="save gate status">
            <div className="workspaceGateTop">
              <strong>{saveBlocked ? "保存未启用" : "保存可用"}</strong>
              <WorkspaceStatusPill status={saveBlocked ? "blocked" : "available"}>
                {saveBlocked ? "只读安全模式" : "允许写入"}
              </WorkspaceStatusPill>
            </div>
            <p>{sourceDisplay.correctionLine}</p>
            {saveBlocked && <code>{sourceDisplay.saveBlockedLine}</code>}
          </article>
        )}

        <WorkspaceWorkflowLink
          documentId={document.document_id}
          chapterId={chapter.chapter_id}
          onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
        />
      </details>
    </div>
  );
}

function pipelineStatusLabel(status) {
  const labels = {
    no_zotero_notes: "没有 Zotero 笔记",
    blocked_no_notes_in_scope: "本章无笔记",
    notes_not_imported: "笔记未导入",
    dry_run_ready: "dry-run 已就绪",
    import_required: "需要导入",
    already_imported: "已导入",
    correction_review_ready: "纠错审核就绪",
    correction_review_in_progress: "纠错审核中",
    correction_review_partially_saved: "纠错部分保存",
    blocked_save_gate: "保存未启用",
    correction_review_complete: "纠错审核完成",
    ready_for_classification: "可进入分类",
  };
  return labels[status] || status || "未知状态";
}

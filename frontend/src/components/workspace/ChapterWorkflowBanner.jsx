import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import WorkspaceWorkflowLink from "./WorkspaceWorkflowLink.jsx";
import { normalizeWorkspaceState } from "../../utils/workspaceStateAdapter.js";

export default function ChapterWorkflowBanner({ state, onOpenAdvancedWorkflow, onViewWorkflowStatus }) {
  const document = state?.document || {};
  const chapter = state?.current_chapter || {};
  const display = normalizeWorkspaceState(state);
  const { workflow, noNotes } = display;
  const headline = buildWorkflowBannerHeadline(display, workflow);
  const subline = buildWorkflowBannerSubline(display, workflow);

  return (
    <section className={`chapterWorkflowBanner ${noNotes ? "locked" : ""}`} aria-label={subline}>
      <div className="chapterWorkflowBannerMain">
        <WorkspaceStatusPill status={workflow.status}>只读安全模式</WorkspaceStatusPill>
        <h2>{headline}</h2>
        <p>只读模式</p>
      </div>
      <div className="chapterWorkflowBannerActions">
        <button className="workspacePillButton" type="button" onClick={onViewWorkflowStatus}>
          流程状态
        </button>
        <WorkspaceWorkflowLink
          documentId={document.document_id}
          chapterId={chapter.chapter_id}
          onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
        >
          高级流程
        </WorkspaceWorkflowLink>
      </div>
    </section>
  );
}

function buildWorkflowBannerHeadline(display, workflow) {
  if (display.noNotes) return "本章没有 Zotero 笔记";
  const existing = Number(display.notes?.existing || 0);
  const correctionSaved = workflow.headline.includes("已保存");
  if (correctionSaved && existing > 0) {
    return `${existing} 条笔记已关联 · 纠错审核已保存`;
  }
  if (workflow.headline.includes("部分保存")) {
    return `${existing || "已关联"} 条笔记 · 纠错审核部分保存`;
  }
  if (workflow.headline.includes("未保存")) {
    return `${existing || "已关联"} 条笔记 · 纠错审核未保存`;
  }
  return workflow.headline || "纠错审核已保存";
}

function buildWorkflowBannerSubline(display, workflow) {
  if (display.noNotes) return "只读模式 · 当前章节没有可审核笔记 · 不写 DB";
  if (display.saveBlocked) return "只读安全模式 · 保存未启用 · 未写入数据库 · 不调用 LLM";
  if (workflow.status === "available") return "只读模式 · 未写入数据库 · 不调用 LLM";
  return workflow.body || "只读模式 · 未写入数据库 · 不调用 LLM";
}

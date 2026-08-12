import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import { normalizeWorkspaceState } from "../../utils/workspaceStateAdapter.js";

const FORBIDDEN_DRAWER_SURFACES = [
  "未挂载保存控件",
  "未挂载往返编辑器",
  "未挂载人工审计工作台",
  "未挂载 Zotero 写回界面",
  "未挂载生成动作",
];

const NOTE_CLASSIFICATION_LABELS = [
  "memory_note",
  "connection_note",
  "mechanism_note",
  "research_idea_note",
  "unclear",
  "needs_manual_review",
];

export default function AdvancedWorkflowDrawer({
  isOpen,
  onClose,
  workspaceState,
  documentId,
  chapterId,
  advancedWorkflowHref,
  onOpenAdvancedWorkflow,
}) {
  if (!isOpen) return null;

  const state = workspaceState || {};
  const document = state.document || {};
  const chapter = state.current_chapter || {};
  const display = normalizeWorkspaceState(state);
  const notes = display.notes || {};
  const correction = display.correction || {};
  const classification = display.classification || state.classification_review_status || {};
  const readiness = display.readiness || {};
  const saved = display.saved || {};
  const searchLayers = display.searchLayers || [];
  const studioCards = display.studioCards || [];
  const noNotes = display.noNotes;
  const savedItems = Number(saved.saved_item_count || correction.saved_items || 0);
  const expectedItems = Number(correction.expected_items || notes.user_notes || 0);
  const savedSections = saved.source_section_ids || correction.saved_sections || saved.partial_saved_sections || correction.partial_saved_sections || [];
  const missingSections = saved.missing_sections || correction.missing_sections || [];
  const pn68WarningPreserved = Boolean(saved.pn68_warning_preserved || correction.pn68_warning_preserved);
  const pn68Warning = saved.pn68_reviewer_warning || correction.pn68_reviewer_warning || "";
  const classificationPackageStatus = saved.classification_package_status || correction.classification_package_status || "blocked";
  const classificationPackageReady = Boolean(saved.classification_package_ready || correction.classification_package_ready);
  const classificationSaved = classification.status === "saved" || correction.classification_review_saved === true;
  const blockerText = (readiness.current_blockers || []).join(", ") || (readiness.production_review_write_allowed === false ? "production_db_write_disabled" : "none");
  const href = advancedWorkflowHref || "#";
  const resolvedDocumentId = documentId || document.document_id;
  const resolvedChapterId = chapterId || chapter.chapter_id;

  function handleOpenAdvancedWorkflow(event) {
    if (!resolvedDocumentId) return;
    event.preventDefault();
    onOpenAdvancedWorkflow?.(resolvedDocumentId, resolvedChapterId);
  }

  return (
    <div className="advancedWorkflowDrawerLayer" aria-label="高级流程抽屉">
      <button className="advancedWorkflowDrawerBackdrop" type="button" aria-label="关闭流程状态抽屉" onClick={onClose} />
      <aside className="advancedWorkflowDrawer" role="dialog" aria-modal="false" aria-label="高级流程状态抽屉">
        <div className="advancedWorkflowDrawerHeader">
          <div>
            <p className="workspaceKicker">高级流程</p>
            <h3>高级流程</h3>
            <span>{chapterHeading(chapter)}</span>
          </div>
          <button className="quietButton advancedWorkflowDrawerClose" type="button" onClick={onClose}>
            关闭
          </button>
        </div>

        <section className="advancedWorkflowDrawerCard" aria-label="当前章节">
          <strong>{chapterHeading(chapter)}</strong>
          <span>{document.title || "未命名文档"}</span>
          {noNotes && <WorkspaceStatusPill status="unavailable">NO_NOTES_IN_SCOPE</WorkspaceStatusPill>}
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="笔记导入状态">
          <div className="advancedWorkflowDrawerSectionHeader">
            <strong>笔记导入状态</strong>
            <WorkspaceStatusPill status={noNotes ? "unavailable" : "available"} />
          </div>
          {noNotes ? (
            <p>NO_NOTES_IN_SCOPE</p>
          ) : (
            <>
              <p>Zotero 笔记：已关联 {Number(notes.existing || 0)} 条</p>
              <p>用户笔记：{Number(notes.user_notes || 0)} 条</p>
              <p>仅证据笔记：{Number(notes.evidence_only || 0)} 条</p>
              <p>导入状态：{labelizeStatus(notes.status || "unknown")}</p>
            </>
          )}
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="纠错审核状态">
          <div className="advancedWorkflowDrawerSectionHeader">
            <strong>纠错审核状态</strong>
            <WorkspaceStatusPill status={noNotes ? "locked" : display.saveBlocked ? "blocked" : "not_saved"} />
          </div>
          <p>纠错审核：{noNotes ? "未启用" : labelizeCorrectionStatus(correction.status || saved.status || "not_saved")}</p>
          <p>{expectedItems ? `已保存条目：${savedItems} / ${expectedItems}` : `已保存条目：${savedItems}`}</p>
          <p>已保存章节：{savedSections.length ? savedSections.join(", ") : "无"}</p>
          <p>缺失章节：{missingSections.length ? missingSections.join(", ") : "无"}</p>
          <p>PN68：{saved.pn68_status || correction.pn68_status || "not_saved"}{pn68WarningPreserved ? " · warning 已保留" : ""}</p>
          <p>允许保存：{readiness.production_review_write_allowed === true ? "是" : "否"}</p>
          <p className={blockerText !== "none" ? "advancedWorkflowDrawerWarning" : ""}>当前条件：{drawerBlockerLabel(blockerText)}</p>
          <p>可进入分类：{saved.ready_for_classification === true ? "是" : "否"}</p>
          <p>分类包：{classificationPackageReady ? "分类 dry-run 已就绪 · 仅预览" : labelizeStatus(classificationPackageStatus)}</p>
          <p>分类审核：{classificationSaved ? `已保存 · ${Number(classification.saved_item_count || correction.classification_saved_item_count || 0)} 条` : "未保存"}</p>
          {classificationSaved && <p>classification_review_id={classification.review_id || correction.classification_review_id}</p>}
          {classificationSaved && <p>对象候选生成：未启用 · 需要显式 Phase7D gate</p>}
          {classificationSaved && <p>PN68 分类：{classification.pn68_classification_label || correction.pn68_classification_label || "needs_manual_review"} · {classification.pn68_confidence || correction.pn68_classification_confidence || "unknown"}</p>}
          {classificationPackageReady && <p>{expectedItems} 条笔记已就绪 · 仅预览 · 分类生成/保存已禁用</p>}
          {classificationPackageReady && <p>标签：{NOTE_CLASSIFICATION_LABELS.join(", ")}</p>}
          {classificationPackageReady && pn68WarningPreserved && <p className="advancedWorkflowDrawerWarning">PN68 warning 已保留 · manual_review_or_unclear_classification</p>}
          {noNotes && <p>本章没有可审计笔记</p>}
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="保存状态摘要">
          <div className="advancedWorkflowDrawerSectionHeader">
            <strong>保存状态摘要</strong>
            <WorkspaceStatusPill status={saved.ready_for_classification ? "available" : "not_saved"} />
          </div>
          <p>review_id={saved.latest_review_id || saved.review_id || "not_saved"}</p>
          <p>confirmed_count={Number(saved.confirmed_count || 0)}</p>
          <p>needs_followup_count={Number(saved.needs_followup_count || 0)}</p>
          <p>ready_for_classification={String(saved.ready_for_classification === true)}</p>
          <p>pn68_warning_preserved={String(pn68WarningPreserved)}</p>
          {pn68Warning && <p className="advancedWorkflowDrawerWarning">pn68_warning={pn68Warning}</p>}
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="检索层可用性">
          <div className="advancedWorkflowDrawerSectionHeader">
            <strong>检索层可用性</strong>
          </div>
          <div className="advancedWorkflowDrawerPillGrid">
            {searchLayers.map((layer) => (
              <span key={layer.id}>{layer.title}: {layer.status}</span>
            ))}
          </div>
          {noNotes && <p>笔记层不可用</p>}
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="Studio 前置条件">
          <div className="advancedWorkflowDrawerSectionHeader">
            <strong>Studio 前置条件</strong>
          </div>
          <div className="advancedWorkflowDrawerPillGrid">
            {studioCards.slice(0, 4).map((card) => (
              <span key={card.id}>{card.title}: {card.reason}</span>
            ))}
          </div>
        </section>

        <section className="advancedWorkflowDrawerCard" aria-label="抽屉安全边界">
          <strong>只读抽屉边界</strong>
          <ul className="advancedWorkflowDrawerForbiddenList">
            {FORBIDDEN_DRAWER_SURFACES.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <a className="workspacePillButton advancedWorkflowDrawerCta" href={href} onClick={handleOpenAdvancedWorkflow}>
          打开高级流程
        </a>
      </aside>
    </div>
  );
}

function chapterHeading(chapter = {}) {
  const index = chapter.chapter_index || "?";
  const cleanTitle = String(chapter.title || "当前章节").replace(/^(\d+)\.\s*/, "$1 ");
  return `章节 ${index} / ${cleanTitle}`;
}

function labelizeStatus(status) {
  if (status === "already_imported") return "已导入";
  if (status === "not_saved") return "未保存";
  return String(status || "unknown").replace(/_/g, " ");
}

function labelizeCorrectionStatus(status) {
  if (status === "not_saved") return "未保存";
  if (status === "locked_no_notes_in_scope") return "未启用";
  if (status === "saved") return "已保存";
  if (status === "partial") return "部分保存";
  return labelizeStatus(status);
}

function drawerBlockerLabel(value) {
  if (value === "production_db_write_disabled") return "只读模式：未写入数据库";
  if (value === "none") return "无";
  return value || "无";
}

import StateMessage from "../../../components/StateMessage.jsx";
import ChapterNoteClassificationPanel from "../../../components/book/ChapterNoteClassificationPanel.jsx";
import ChapterNoteCorrectionPanel from "../../../components/book/ChapterNoteCorrectionPanel.jsx";
import ChapterNotesImportStepper from "../../../components/book/ChapterNotesImportStepper.jsx";
import NoteFirstWorkflowSteps from "../../../components/book/NoteFirstWorkflowSteps.jsx";
import ObjectCandidateHumanReviewWorkbench from "../../../components/book/ObjectCandidateHumanReviewWorkbench.jsx";
import RelationCandidateDryRunPreviewPanel from "../../../components/book/RelationCandidateDryRunPreviewPanel.jsx";
import TriSourceObjectPreviewPanel from "../../../components/book/TriSourceObjectPreviewPanel.jsx";
import { buildBookNoteFirstGate } from "../../../utils/noteFirstWorkflow.js";
import {
  chapterListKey,
  chapterStatusLabel,
  chapterTitle,
  pageRange,
} from "../utils/bookDetail.js";

export function BookUnitProcessingPanel({ chapters, dryRuns, onRunDryRun }) {
  return (
    <details className="unitProcessingPanel bookUnitProcessingPanel bookUnitProcessingPanelCollapsed" aria-label="高级状态 / 开发者状态：按章只读 diagnostics">
      <summary>
        <span>高级状态 / 开发者状态：全部章节只读摘要</span>
        <small>book_chapter diagnostics · 展开查看各章双源机制来源状态</small>
      </summary>
      <div className="unitProcessingList">
        {chapters.map((chapter) => {
          const gate = buildBookNoteFirstGate(chapter, "本章");
          return (
            <article key={chapterListKey("unit", chapter)} className="unitProcessingCard">
              <div className="unitProcessingMain">
                <span className="unitTypeBadge">book_chapter</span>
                <h4>{chapterTitle(chapter)}</h4>
                <p>{pageRange(chapter)} · 双源流程：Zotero 笔记与原文片段进入对象审核，最后机制审核</p>
              </div>
              <div className="unitProcessingMetrics">
                <MetricMini label="Zotero annotations" value={gate.annotationCount} />
                <MetricMini label="用户笔记" value={gate.userNoteCount} />
                <MetricMini label="仅高亮证据" value={gate.evidenceOnlyCount} />
                <MetricMini label="已同步到 Search" value={gate.syncedNoteCount} />
                <MetricMini label="note correction gate" value={gate.canCorrectNotes ? "ready" : "blocked"} />
                <MetricMini label="object candidate gate" value={gate.canGenerateObjects ? "ready" : "blocked"} />
                <MetricMini label="object candidates count" value={chapter.object_count || 0} />
                <MetricMini label="reviewed object count" value={gate.reviewedObjectCount} />
                <MetricMini label="mechanism readiness" value={gate.reviewedObjectCount ? "ready gate" : "blocked"} />
              </div>
              <p className="unitSourceNotice">Zotero annotations / 用户笔记 / 仅高亮证据均指已同步到 Search 的 notes；source=zotero_plugin 只作为 legacy desktop capture 显示。</p>
              {gate.evidenceOnlyCount > 0 && (
                <p className="unitEvidenceNotice">有 {gate.evidenceOnlyCount} 条 Zotero 高亮没有笔记内容；不进入笔记纠错/分类审核，但可作为 source-led 原文片段来源。</p>
              )}
              <NoteFirstWorkflowSteps gate={gate} compact />
              <ReviewGateSummary />
              <p className="mechanismGateNotice">mechanism_blocked_until_objects_reviewed：对象审核完成后可生成机制候选。</p>
              <ChapterZoteroDryRunCard chapter={chapter} dryRunState={dryRuns[String(chapter.chapter_id)]} onRun={() => onRunDryRun(chapter)} compact />
            </article>
          );
        })}
      </div>
    </details>
  );
}

export function ReviewGateSummary() {
  return (
    <div className="reviewGateSummary" aria-label="机制层审核状态">
      <strong>机制层保持阻断</strong>
      <span>evidence_review</span>
      <span>abstraction_review</span>
      <span>classification_review</span>
      <span>relationship_review</span>
      <span>search_entry_review</span>
      <small>mechanism_review layers remain blocked until reviewed objects exist.</small>
    </div>
  );
}

export function DisabledAction({ label, reason }) {
  return (
    <span className="disabledActionWithReason">
      <button type="button" disabled title={reason}>{label}</button>
      <small>{reason}</small>
    </span>
  );
}

export function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

export function BookNoteFirstWorkspace({
  book,
  chapters,
  activeChapter,
  activeChapterId,
  setSelectedChapterId,
  dryRunState,
  zoteroApplyState,
  zoteroImportConfirmed,
  onRunDryRun,
  onZoteroImportConfirmationChange,
  onConfirmZoteroNotesImport,
  noteCorrectionPlanState,
  noteCorrectionMode,
  noteCorrectionBatchSize,
  noteCorrectionSelectedSection,
  noteCorrectionSelectedBatch,
  noteCorrectionPackageStates,
  copiedNoteCorrectionPackageKey,
  noteCorrectionReviewTexts,
  noteCorrectionReviewValidationStates,
  noteCorrectionReviewFilters,
  noteCorrectionSaveStates,
  noteCorrectionSaveReadinessStates,
  workspaceState,
  onLoadNoteCorrectionPlan,
  onSelectNoteCorrectionMode,
  onSelectNoteCorrectionSection,
  onSelectNoteCorrectionBatchSize,
  onSelectNoteCorrectionBatch,
  onPreviewNoteCorrectionPackage,
  onCopyNoteCorrectionPackage,
  onNoteCorrectionReviewTextChange,
  onValidateNoteCorrectionReview,
  onSetNoteCorrectionReviewFilter,
  onLoadNoteCorrectionSaveReadiness,
  onSaveNoteCorrectionReview,
  noteClassificationPackageState,
  copiedNoteClassificationPackage,
  noteClassificationReviewText,
  noteClassificationReviewValidationState,
  onPreviewNoteClassificationPackage,
  onCopyNoteClassificationPackage,
  onNoteClassificationReviewTextChange,
  onValidateNoteClassificationReview,
  triSourceObjectPackageState,
  onPreviewTriSourceObjectPackage,
  relationCandidateDryRunPackageState,
  onPreviewRelationCandidateDryRun,
  objectCandidateHumanReviewWorkbenchState,
  objectCandidateHumanReviewValidationState,
  onLoadObjectCandidateHumanReviewWorkbench,
  onValidateObjectCandidateHumanReview,
}) {
  const selectableChapters = chapters;
  const gate = buildBookNoteFirstGate(activeChapter || {}, "本章");

  return (
    <section className="bookImportWorkspace noteFirstWorkspace">
      <div className="sectionHeader">
        <h3>R3 Notes Import Linear Flow</h3>
        <span>Zotero 笔记导入 Search</span>
      </div>

      <div className="noteFirstWorkspaceGrid">
        <div className="noteFirstPanel noteFirstCurrentPanel">
          <div className="noteFirstPanelHeader">
            <span>当前选择</span>
            <strong>{activeChapter ? chapterTitle(activeChapter) : "暂无章节"}</strong>
          </div>
          <div className="noteFirstSelectionRows">
            <div className="objectDocumentLine">
              <span>当前书</span>
              <strong>{book.title}</strong>
            </div>
            <label className="bookChapterSelect">
              <span>当前章节</span>
              <select
                value={activeChapterId || ""}
                onChange={(event) => setSelectedChapterId(Number(event.target.value))}
              >
                {selectableChapters.map((chapter) => (
                  <option key={chapterListKey(book.document_id, chapter)} value={chapter.chapter_id}>
                    {chapterTitle(chapter)}
                  </option>
                ))}
              </select>
            </label>
            <div className="objectDocumentLine">
              <span>章节状态</span>
              <strong>{activeChapter ? `${chapterStatusLabel(activeChapter.object_import_status)} · ${activeChapter.evidence_count || 0} 证据` : "暂无章节"}</strong>
            </div>
          </div>
        </div>

        <div className="noteFirstPanel noteFirstWorkflowPanel noteFirstPrimaryFlowPanel">
          <ChapterWorkspaceStateBanner state={workspaceState} />
          <ChapterNotesImportStepper
            book={book}
            chapter={activeChapter}
            gate={gate}
            workspaceState={workspaceState}
            dryRunState={dryRunState}
            applyState={zoteroApplyState}
            confirmChecked={zoteroImportConfirmed}
            onRunDryRun={onRunDryRun}
            onConfirmCheckedChange={onZoteroImportConfirmationChange}
            onConfirmImport={onConfirmZoteroNotesImport}
          >
            <ChapterNoteCorrectionPanel
              gate={gate}
              chapter={activeChapter}
              planState={noteCorrectionPlanState}
              selectedMode={noteCorrectionMode}
              batchSize={noteCorrectionBatchSize}
              selectedSectionId={noteCorrectionSelectedSection}
              selectedBatchIndex={noteCorrectionSelectedBatch}
              packageStates={noteCorrectionPackageStates}
              copiedPackageKey={copiedNoteCorrectionPackageKey}
              reviewTexts={noteCorrectionReviewTexts}
              validationStates={noteCorrectionReviewValidationStates}
              reviewFilters={noteCorrectionReviewFilters}
              saveStates={noteCorrectionSaveStates}
              saveReadinessStates={noteCorrectionSaveReadinessStates}
              savedReviewState={workspaceState?.data?.saved_review_state || null}
              onLoadPlan={onLoadNoteCorrectionPlan}
              onSelectMode={onSelectNoteCorrectionMode}
              onSelectSection={onSelectNoteCorrectionSection}
              onSelectBatchSize={onSelectNoteCorrectionBatchSize}
              onSelectBatch={onSelectNoteCorrectionBatch}
              onPreview={onPreviewNoteCorrectionPackage}
              onCopy={onCopyNoteCorrectionPackage}
              onReviewTextChange={onNoteCorrectionReviewTextChange}
              onValidateReview={onValidateNoteCorrectionReview}
              onSetReviewFilter={onSetNoteCorrectionReviewFilter}
              onLoadSaveReadiness={onLoadNoteCorrectionSaveReadiness}
              onSaveReview={onSaveNoteCorrectionReview}
            />
          </ChapterNotesImportStepper>

          <ChapterNoteClassificationPanel
            chapter={activeChapter}
            packageState={noteClassificationPackageState}
            copied={copiedNoteClassificationPackage}
            reviewText={noteClassificationReviewText}
            validationState={noteClassificationReviewValidationState}
            onPreview={onPreviewNoteClassificationPackage}
            onCopy={onCopyNoteClassificationPackage}
            onReviewTextChange={onNoteClassificationReviewTextChange}
            onValidateReview={onValidateNoteClassificationReview}
          />

          <TriSourceObjectPreviewPanel
            chapter={activeChapter}
            packageState={triSourceObjectPackageState}
            onPreview={onPreviewTriSourceObjectPackage}
          />

          <ObjectCandidateHumanReviewWorkbench
            chapter={activeChapter}
            workbenchState={objectCandidateHumanReviewWorkbenchState}
            validationState={objectCandidateHumanReviewValidationState}
            onLoad={onLoadObjectCandidateHumanReviewWorkbench}
            onValidate={onValidateObjectCandidateHumanReview}
          />

          <RelationCandidateDryRunPreviewPanel
            chapter={activeChapter}
            packageState={relationCandidateDryRunPackageState}
            onPreview={onPreviewRelationCandidateDryRun}
          />
        </div>

        <details className="bookAdvancedStatusDetails noteFirstPanel">
          <summary>
            <span>高级状态 / 开发者状态</span>
            <small>双源 workflow、旧 Step 4-9、只读 diagnostics</small>
          </summary>
          <div className="bookAdvancedStatusBody">
            <div className="noteFirstPanelHeader">
              <span>只读状态诊断</span>
              <strong>{gate.hasSyncedNotes ? "已导入 Search 或已存在" : "尚未导入 Search"}</strong>
            </div>
            <div className="unitProcessingMetrics">
              <MetricMini label="Zotero annotations" value={gate.annotationCount} />
              <MetricMini label="用户笔记" value={gate.userNoteCount} />
              <MetricMini label="仅高亮证据" value={gate.evidenceOnlyCount} />
              <MetricMini label="已同步到 Search" value={gate.syncedNoteCount} />
              <MetricMini label="笔记纠错 gate" value={gate.canCorrectNotes ? "ready" : "blocked"} />
              <MetricMini label="对象候选 gate" value="locked" />
            </div>
            {gate.evidenceOnlyCount > 0 && (
              <p className="unitEvidenceNotice">有 {gate.evidenceOnlyCount} 条 Zotero 高亮没有笔记内容，只作为 supporting_evidence，不进入主纠错 candidates。</p>
            )}
            <NoteFirstWorkflowSteps gate={gate} />
            <p className="mechanismGateNotice">旧 Step 4-9 diagnostic only：classification/object/relation/mechanism locked，本阶段不生成、不预览、不保存。</p>
            <p className="mechanismGateNotice">tri-source object candidate gate planned_not_implemented：笔记对象等待纠错/分类；高光对象基于高光证据；全文章节对象基于章节 chunks；统一 object_review 前不合并入库。</p>
            <p className="mechanismGateNotice">mechanism_blocked_until_objects_reviewed：对象审核完成后才进入机制候选。</p>
            <ReviewGateSummary />
            <ChapterZoteroDryRunCard chapter={activeChapter} dryRunState={dryRunState} onRun={onRunDryRun} compact />
            <LegacyChapterBundleNotice gate={gate} />
          </div>
        </details>
      </div>
    </section>
  );
}

export function ChapterWorkspaceStateBanner({ state }) {
  const data = state?.data || null;
  if (state?.status === "loading" && !data) {
    return <p className="chapterWorkspaceStateBanner">Loading workspace state...</p>;
  }
  if (state?.status === "error") {
    return <StateMessage title="Workspace state 不可用" body={state.error} />;
  }
  if (!data) return null;
  const notes = data.notes_import_status || {};
  const correction = data.correction_review_status || {};
  const readiness = data.save_readiness || {};
  const noNotes = notes.status === "blocked_no_notes_in_scope";
  const reviewLabel = noNotes
    ? "NO_NOTES_IN_SCOPE · correction review locked"
    : `${notes.existing ?? 0} notes linked · correction review ${correction.status || "not_saved"} · save ${readiness.production_review_write_allowed ? "allowed" : "blocked"}`;
  return (
    <section className={`chapterWorkspaceStateBanner ${noNotes ? "locked" : ""}`} aria-label="chapter workspace state">
      <strong>{reviewLabel}</strong>
      {!noNotes && (
        <span>
          {notes.user_notes ?? 0} user notes · {notes.evidence_only ?? 0} evidence-only
          {!readiness.production_review_write_allowed && ` · ${(readiness.current_blockers || []).join(", ") || "write_not_allowed"}`}
        </span>
      )}
      {noNotes && <span>blocked_no_notes_in_scope</span>}
    </section>
  );
}

export function ChapterZoteroDryRunCard({ chapter, dryRunState, onRun, compact = false }) {
  const data = dryRunState?.data || null;
  const loading = dryRunState?.status === "loading";
  const buttonLabel = loading ? "读取中..." : data ? "重新读取 Zotero notes" : "读取 Zotero notes";
  if (compact) {
    return (
      <div className="chapterZoteroDryRunInline" aria-label="Zotero notes 只读检查">
        <button type="button" onClick={onRun} disabled={loading || !chapter?.chapter_id}>
          {buttonLabel}
        </button>
        <span>只读辅助入口；不 apply，不写 Search DB，不写 Zotero DB，不生成对象候选，不生成机制。</span>
        {dryRunState?.status === "error" && <StateMessage title="dry-run 失败" body={dryRunState.error} />}
        {data && (
          <details className="chapterZoteroDryRunDetails">
            <summary>查看 dry-run 详情</summary>
            <ChapterZoteroDryRunMetrics data={data} />
          </details>
        )}
      </div>
    );
  }
  return (
    <div className="chapterZoteroDryRunCard" aria-label="Zotero notes 只读检查">
      <div className="chapterZoteroDryRunHeader">
        <div>
          <strong>Zotero notes 只读检查</strong>
          <span>{chapterTitle(chapter || {})} · {pageRange(chapter || {})}</span>
        </div>
        <button type="button" onClick={onRun} disabled={loading || !chapter?.chapter_id}>
          {buttonLabel}
        </button>
      </div>
      <p className="unitSourceNotice">只读辅助入口；不 apply，不写 Search DB，不写 Zotero DB，不生成对象候选，不生成机制。</p>
      {dryRunState?.status === "error" && <StateMessage title="dry-run 失败" body={dryRunState.error} />}
      {data && (
        <details className="chapterZoteroDryRunDetails">
          <summary>查看 dry-run 详情</summary>
          <ChapterZoteroDryRunMetrics data={data} />
        </details>
      )}
    </div>
  );
}

export function ChapterZoteroDryRunMetrics({ data }) {
  return (
    <>
      <div className="unitProcessingMetrics">
        <MetricMini label="Zotero annotations dry-run count" value={data.chapter_annotations_count ?? 0} />
        <MetricMini label="用户笔记 count" value={data.chapter_user_note_count ?? 0} />
        <MetricMini label="仅高亮证据 count" value={data.chapter_evidence_only_count ?? 0} />
        <MetricMini label="would_insert_count" value={data.would_insert_count ?? 0} />
        <MetricMini label="note correction review" value={data.note_first_gates?.note_correction_review || "unknown"} />
        <MetricMini label="object candidate gate" value={data.note_first_gates?.object_candidate_generation || "blocked"} />
      </div>
      <div className="reviewGateSummary">
        <span>db_write_performed={String(data.db_write_performed)}</span>
        <span>zotero_db_write_performed={String(data.zotero_db_write_performed)}</span>
        <span>object_candidates_generated={String(data.object_candidates_generated)}</span>
        <span>mechanism_generated={String(data.mechanism_generated)}</span>
      </div>
    </>
  );
}

export function LegacyChapterBundleNotice({ gate }) {
  return (
    <details className="legacyChapterBundleNotice">
      <summary>旧对象包已停用</summary>
      <p>旧版只基于 chunk evidence 的对象包已停用；新版将拆成高光对象和全文章节对象两路，并与笔记对象合并。</p>
      <p>LEGACY_CHAPTER_OBJECT_BUNDLE 只包含 allowed chunk_id evidence，不包含 Zotero notes / user note text。</p>
      <p>当前三路对象候选仍是 planned / not_implemented，不会生成对象包、关系包或机制包。</p>
      <small>当前 reason={gate.objectCandidateReason}</small>
    </details>
  );
}

export function Metric({ label, value }) {
  return (
    <div className="bookMetric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

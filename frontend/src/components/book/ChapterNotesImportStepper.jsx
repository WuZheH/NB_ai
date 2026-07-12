import { useEffect } from "react";
import StateMessage from "../StateMessage.jsx";

export const CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT = "import_zotero_notes_to_notebook_ai";

export default function ChapterNotesImportStepper({
  book,
  chapter,
  gate,
  workspaceState,
  dryRunState,
  applyState,
  confirmChecked = false,
  onRunDryRun,
  onConfirmCheckedChange,
  onConfirmImport,
  children,
}) {
  const dryRunData = dryRunState?.data || null;
  const workspaceData = workspaceState?.data || null;
  const applyData = applyState?.data || null;
  const dryRunLoading = dryRunState?.status === "loading";
  const applyLoading = applyState?.status === "loading";
  const wouldInsert = numberValue(dryRunData?.would_insert_count);
  const wouldSkipExisting = numberValue(
    dryRunData?.would_skip_existing_count ?? workspaceData?.notes_import_status?.would_skip_existing
  );
  const wouldBlock = numberValue(dryRunData?.would_block_count);
  const noNotesInScope = workspaceData?.notes_import_status?.status === "blocked_no_notes_in_scope"
    || dryRunData?.status === "NO_NOTES_IN_SCOPE"
    || dryRunData?.reason === "no_notes_in_scope";
  const alreadyImported = workspaceData?.notes_import_status?.status === "already_imported"
    || (!!dryRunData && !noNotesInScope && wouldInsert === 0 && wouldSkipExisting > 0 && wouldBlock === 0);
  const applySucceeded = applyData?.status === "OK" || applyData?.status === "ALREADY_IMPORTED";
  const needsImport = !!dryRunData && wouldInsert > 0 && wouldBlock === 0 && !applySucceeded;
  const importReady = alreadyImported || applySucceeded;
  const canConfirmImport = needsImport && confirmChecked && !applyLoading;
  const reviewReady = !noNotesInScope && importReady && gate?.canCorrectNotes;
  const reviewLockedReason = noNotesInScope
    ? "no_notes_in_scope"
    : importReady ? (gate?.noteCorrectionReason || "no_user_notes_for_note_review") : "notes_not_imported";
  const stepStatuses = buildStepStatuses({
    chapter,
    dryRunData,
    dryRunLoading,
    needsImport,
    alreadyImported,
    importReady,
    applyLoading,
    reviewReady,
    noNotesInScope,
  });

  useEffect(() => {
    if (!chapter?.chapter_id) return;
    if (dryRunState?.status || dryRunState?.data || dryRunState?.error) return;
    onRunDryRun?.();
  }, [chapter?.chapter_id, dryRunState?.data, dryRunState?.error, dryRunState?.status, onRunDryRun]);

  return (
    <section className="chapterNotesImportStepper" aria-label="Zotero 笔记导入 NOTEBOOK_AI 线性 Stepper">
      <div className="chapterNotesImportHeader">
        <div>
          <span>R3 Notes Import Linear Flow</span>
          <strong>Zotero 笔记导入 NOTEBOOK_AI</strong>
        </div>
        <div className="chapterNotesImportSafety">
          <span>只读读取 Zotero notes</span>
          <span>不会修改 Zotero 原始笔记</span>
          <span>不写 Zotero</span>
          <span>不调用 LLM</span>
          <span>不进入 classification/object/relation/mechanism</span>
          <span>不写 vector store</span>
        </div>
      </div>

      <nav className="chapterNotesImportStepperNav" aria-label="Zotero notes import stepper">
        {[
          "Select note import scope",
          "Zotero notes read-only preflight",
          "Confirm import NOTEBOOK_AI",
          "Import result / already existing",
          "Enter ChatGPT note correction review mode",
        ].map((label, index) => (
          <span key={label} className={stepStatuses[index]} data-step-status={stepStatuses[index]}>
            <strong>{index + 1}</strong>
            {label}
          </span>
        ))}
      </nav>

      <div className="chapterNotesImportSteps">
        <section className="chapterNotesImportStep">
          <StepTitle number={1} title="Select note import scope" status={stepStatuses[0]} />
          <div className="unitProcessingMetrics">
            <MetricMini label="document_id" value={book?.document_id || dryRunData?.document_id || "unknown"} />
            <MetricMini label="chapter_id" value={chapter?.chapter_id || dryRunData?.chapter_id || "unknown"} />
            <MetricMini label="book_chapter" value={chapterTitle(chapter || dryRunData || {})} />
            <MetricMini label="page scope" value={pageRange(chapter || dryRunData || {})} />
            <MetricMini label="zotero_item_key" value={dryRunData?.zotero_item_key || book?.zotero_item_key || "读取后显示"} />
            <MetricMini label="zotero_attachment_key" value={dryRunData?.zotero_attachment_key || book?.zotero_attachment_key || "读取后显示"} />
          </div>
          <p className="unitSourceNotice">范围固定为当前 book_chapter；Zotero annotation key 只作为来源证据显示，导入身份以 NOTEBOOK_AI note id 为准。</p>
        </section>

        <section className="chapterNotesImportStep">
          <StepTitle number={2} title="Zotero notes read-only preflight" status={stepStatuses[1]} />
          <div className="chapterNotesImportActionRow">
            <button type="button" onClick={onRunDryRun} disabled={dryRunLoading || !chapter?.chapter_id}>
              {dryRunLoading ? "读取中..." : "读取 Zotero notes"}
            </button>
            <span>GET /zotero-notes/dry-run；只读预检，不 apply，不写 NOTEBOOK_AI DB，不写 Zotero DB。</span>
          </div>
          {dryRunState?.status === "error" && <StateMessage title="Zotero notes preflight 失败" body={dryRunState.error} />}
          {dryRunData && <DryRunMetrics data={dryRunData} />}
        </section>

        <section className="chapterNotesImportStep">
          <StepTitle number={3} title="Confirm import NOTEBOOK_AI" status={stepStatuses[2]} />
          {dryRunData ? (
            <>
              {alreadyImported && (
                <p className="chapterNotesImportSuccess">本章 Zotero 笔记已导入 NOTEBOOK_AI：would_insert=0，would_skip_existing={wouldSkipExisting}。无需再次导入，可直接进入笔记纠错审核。</p>
              )}
              {wouldBlock > 0 && (
                <p className="chapterNotesImportDanger">dry-run 发现 blocked annotations={wouldBlock}，本阶段不允许导入。</p>
              )}
              {needsImport && (
                <>
                  <label className="chapterNotesImportConfirm">
                    <input
                      type="checkbox"
                      checked={!!confirmChecked}
                      onChange={(event) => onConfirmCheckedChange?.(event.target.checked)}
                      disabled={applyLoading}
                    />
                    <span>确认只向 NOTEBOOK_AI 导入 {wouldInsert} 条 Zotero notes；confirm_write=true；confirmation_context={CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT}</span>
                  </label>
                  <button type="button" onClick={onConfirmImport} disabled={!canConfirmImport}>
                    {applyLoading ? "导入中..." : "导入到 NOTEBOOK_AI"}
                  </button>
                </>
              )}
              {!alreadyImported && !needsImport && wouldBlock === 0 && (
                <p className={noNotesInScope ? "chapterNotesImportNoNotes" : "unitSourceNotice"}>
                  {noNotesInScope ? "当前章没有 Zotero 笔记可导入。" : "当前 dry-run 没有可导入的用户笔记。"}
                </p>
              )}
            </>
          ) : (
            <p className="unitSourceNotice">先执行上方只读 preflight。</p>
          )}
          {applyState?.status === "error" && <StateMessage title="导入请求失败" body={applyState.error} />}
          {applyData?.status === "BLOCKED" && (
            <p className="chapterNotesImportDanger">apply blocked：{applyData.apply_blocked_reason || "unknown"}。</p>
          )}
        </section>

        <section className="chapterNotesImportStep">
          <StepTitle number={4} title="Import result / already existing" status={stepStatuses[3]} />
          <ImportResult dryRunData={dryRunData} applyData={applyData} />
        </section>

        <section className="chapterNotesImportStep chapterNotesImportReviewStep">
          <StepTitle number={5} title="Enter ChatGPT note correction review mode" status={stepStatuses[4]} />
          <p className="unitSourceNotice">审核入口只开放 full_chapter / section_scoped / fixed_size_batch 的 note_correction_review；校验不会自动保存，人工审计后必须显式点击保存按钮。</p>
          {workspaceData && (
            <div className="reviewGateSummary" aria-label="workspace saved review state">
              <span>workspace_review_status={workspaceData.correction_review_status?.status || "unknown"}</span>
              <span>saved_items={workspaceData.saved_review_state?.saved_item_count ?? 0}/{workspaceData.correction_review_status?.expected_items ?? workspaceData.notes_import_status?.user_notes ?? 0}</span>
              <span>saved_sections={(workspaceData.saved_review_state?.source_section_ids || workspaceData.correction_review_status?.saved_sections || []).join(", ") || "none"}</span>
              <span>missing_sections={(workspaceData.saved_review_state?.missing_sections || workspaceData.correction_review_status?.missing_sections || []).join(", ") || "none"}</span>
              <span>pn68_status={workspaceData.saved_review_state?.pn68_status || workspaceData.correction_review_status?.pn68_status || "not_saved"}</span>
              <span>pn68_warning_preserved={String(Boolean(workspaceData.saved_review_state?.pn68_warning_preserved || workspaceData.correction_review_status?.pn68_warning_preserved))}</span>
              <span>ready_for_classification={String(Boolean(workspaceData.saved_review_state?.ready_for_classification || workspaceData.correction_review_status?.ready_for_classification))}</span>
              <span>classification_package={workspaceData.saved_review_state?.classification_package_status || workspaceData.correction_review_status?.classification_package_status || "blocked"}</span>
              <span>production_review_write_allowed={String(workspaceData.save_readiness?.production_review_write_allowed === true)}</span>
            </div>
          )}
          {reviewReady ? (
            children
          ) : (
            <div className="chapterNotesImportLocked" aria-label="note correction review locked">
              <strong>{noNotesInScope ? "NO_NOTES_IN_SCOPE" : "notes_not_imported"}</strong>
              <span>{reviewLockedReason}</span>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

function StepTitle({ number, title, status }) {
  return (
    <div className="chapterNotesImportStepTitle">
      <span>{number}</span>
      <strong>{title}</strong>
      <em>{status}</em>
    </div>
  );
}

function DryRunMetrics({ data }) {
  return (
    <>
      <div className="unitProcessingMetrics">
        <MetricMini label="total annotations" value={data.total_annotations_in_attachment ?? 0} />
        <MetricMini label="chapter annotations" value={data.chapter_annotations_count ?? 0} />
        <MetricMini label="user notes" value={data.chapter_user_note_count ?? 0} />
        <MetricMini label="evidence-only" value={data.chapter_evidence_only_count ?? 0} />
        <MetricMini label="empty note text" value={data.chapter_empty_note_text_count ?? 0} />
        <MetricMini label="with page" value={data.chapter_annotations_with_page_count ?? 0} />
        <MetricMini label="without page" value={data.chapter_annotations_without_page_count ?? 0} />
        <MetricMini label="NOTEBOOK_AI existing" value={data.would_skip_existing_count ?? 0} />
        <MetricMini label="would_insert" value={data.would_insert_count ?? 0} />
        <MetricMini label="would_block" value={data.would_block_count ?? 0} />
      </div>
      <div className="reviewGateSummary">
        <span>db_write_performed={String(data.db_write_performed)}</span>
        <span>zotero_db_write_performed={String(data.zotero_db_write_performed)}</span>
        <span>llm_called={String(data.llm_called)}</span>
        <span>object_candidates_generated={String(data.object_candidates_generated)}</span>
        <span>mechanism_generated={String(data.mechanism_generated)}</span>
      </div>
    </>
  );
}

function ImportResult({ dryRunData, applyData }) {
  if (!dryRunData && !applyData) {
    return <p className="unitSourceNotice">等待只读 preflight 结果。</p>;
  }
  const source = applyData || dryRunData;
  const noNotesInScope = source?.status === "NO_NOTES_IN_SCOPE" || source?.reason === "no_notes_in_scope";
  const mappings = dryRunData?.candidate_mappings || [];
  const unmatchedMappings = mappings.filter((item) => !item.matched_chunk_id);
  const skippedExisting = dryRunData?.skipped_existing || [];
  const blocked = dryRunData?.blocked || applyData?.blocked || [];
  const mappingSummary = dryRunData?.mapping_quality_summary || {};
  if (noNotesInScope) {
    return (
      <div className="chapterNotesImportNoNotes" aria-label="NO_NOTES_IN_SCOPE">
        <strong>NO_NOTES_IN_SCOPE</strong>
        <span>{source.message || "当前章没有 Zotero 笔记可导入。"}</span>
        <span>reason={source.reason || "no_notes_in_scope"}</span>
      </div>
    );
  }
  return (
    <>
      <div className="unitProcessingMetrics">
        <MetricMini label="status" value={source.status || "unknown"} />
        <MetricMini label="inserted" value={applyData?.inserted_count ?? 0} />
        <MetricMini label="skipped existing" value={applyData?.skipped_existing_count ?? dryRunData?.would_skip_existing_count ?? 0} />
        <MetricMini label="blocked" value={applyData?.blocked_count ?? dryRunData?.would_block_count ?? 0} />
        <MetricMini label="integrity" value={source.status === "OK" || source.status === "ALREADY_IMPORTED" ? "ok" : source.status || "pending"} />
        <MetricMini label="duplicate policy" value={source.duplicate_policy || "unknown"} />
        <MetricMini label="sync write" value={String(source.db_write_performed)} />
        <MetricMini label="matched chapter" value={mappingSummary.matched_target_chapter_count ?? 0} />
        <MetricMini label="unmatched" value={mappingSummary.unmatched_count ?? unmatchedMappings.length} />
      </div>
      <CollapsedList title="skipped_existing duplicate/sync list" items={skippedExisting} renderItem={renderNoteIdentity} />
      <CollapsedList title="blocked import list" items={blocked} renderItem={renderNoteIdentity} />
      <CollapsedList title="evidence alignment / unmatched list" items={unmatchedMappings} renderItem={renderMapping} />
      {!!(dryRunData?.warnings || applyData?.warnings || []).length && (
        <CollapsedList title="warnings" items={dryRunData?.warnings || applyData?.warnings || []} renderItem={(warning) => <span>{warning}</span>} />
      )}
    </>
  );
}

function CollapsedList({ title, items, renderItem }) {
  if (!items.length) return null;
  const visible = items.slice(0, 10);
  const remaining = items.length - visible.length;
  return (
    <details className="chapterNotesImportDetails">
      <summary>{title}: {items.length} total, showing {visible.length}</summary>
      <div>
        {visible.map((item, index) => (
          <div key={`${title}-${index}`} className="chapterNotesImportDetailItem">
            {renderItem(item)}
          </div>
        ))}
        {remaining > 0 && <p className="unitSourceNotice">... {remaining} more</p>}
      </div>
    </details>
  );
}

function renderNoteIdentity(item) {
  const noteIdentity = item.server_note_id || item.client_note_id || item.source_note_id || item.note_id;
  const missingReason = item.identity_lookup_reason || item.reason || "note_identity_missing";
  return (
    <>
      <span>{noteIdentity || `note_id_missing: ${missingReason}`}</span>
      <span>{item.zotero_annotation_key || "annotation_key_unknown"}</span>
      <span>{item.reason || item.sync_status || item.note_processing_role || "existing"}</span>
    </>
  );
}

function renderMapping(item) {
  return (
    <>
      <span>{item.zotero_annotation_key || item.source_note_id || "annotation_key_unknown"}</span>
      <span>p.{item.page || "?"}</span>
      <span>matched_chunk_id={item.matched_chunk_id || "null"}</span>
      <span>{(item.warnings || []).join(", ") || item.match_status || "unmatched"}</span>
    </>
  );
}

function buildStepStatuses({ chapter, dryRunData, dryRunLoading, needsImport, alreadyImported, importReady, applyLoading, reviewReady, noNotesInScope }) {
  return [
    chapter?.chapter_id ? "done" : "locked",
    dryRunData ? "done" : dryRunLoading ? "active" : "pending",
    noNotesInScope ? "locked" : importReady ? "done" : needsImport || applyLoading ? "active" : dryRunData ? "pending" : "locked",
    noNotesInScope ? "no_notes" : importReady ? "done" : dryRunData ? alreadyImported ? "done" : "pending" : "locked",
    reviewReady ? "active" : "locked",
  ];
}

function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function chapterTitle(chapter) {
  if (!chapter?.chapter_index && !chapter?.chapter_title && !chapter?.title) return "当前章节";
  return `第 ${chapter.chapter_index || "?"} 章 · ${chapter.title || chapter.chapter_title || "未命名章节"}`;
}

function pageRange(chapter) {
  const start = chapter?.pdf_page_start ?? chapter?.page_start;
  const end = chapter?.pdf_page_end ?? chapter?.page_end;
  if (!start && !end) return "页码暂不可用";
  if (start === end || !end) return `p.${start}`;
  return `p.${start}-${end}`;
}

function numberValue(value) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

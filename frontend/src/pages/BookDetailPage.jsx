import { useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "../api/client.js";
import StateMessage from "../components/StateMessage.jsx";
import { buildNoteClassificationCopyPrompt } from "../components/book/ChapterNoteClassificationPanel.jsx";
import { buildNoteCorrectionCopyPrompt } from "../components/book/ChapterNoteCorrectionPanel.jsx";
import { CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT } from "../components/book/ChapterNotesImportStepper.jsx";
import {
  BookNoteFirstWorkspace,
  BookUnitProcessingPanel,
  ChapterZoteroDryRunCard,
  Metric,
} from "../features/library/components/BookDetailContent.jsx";
import {
  apiErrorMessage,
  chapterListKey,
  chapterStatusLabel,
  chapterTitle,
  chapteredDocumentTypeLabel,
  copyTextToClipboard,
  noteCorrectionPackageUrl,
  noteCorrectionScopeKey,
  noteCorrectionValidateBody,
  noteCorrectionValidateRoute,
  pageRange,
} from "../features/library/utils/bookDetail.js";

export default function BookDetailPage({ state, onBack, onOpenWorkspace, initialChapterId = null, initialWorkflow = "" }) {
  const book = state.data?.book_detail;
  const [expandedChapterId, setExpandedChapterId] = useState(null);
  const [showProcessingWorkspace, setShowProcessingWorkspace] = useState(true);
  const [selectedChapterId, setSelectedChapterId] = useState(null);
  const [zoteroDryRuns, setZoteroDryRuns] = useState({});
  const [zoteroApplyStates, setZoteroApplyStates] = useState({});
  const [zoteroImportConfirmations, setZoteroImportConfirmations] = useState({});
  const [noteCorrectionPlans, setNoteCorrectionPlans] = useState({});
  const [noteCorrectionModes, setNoteCorrectionModes] = useState({});
  const [noteCorrectionBatchSizes, setNoteCorrectionBatchSizes] = useState({});
  const [noteCorrectionSelectedSections, setNoteCorrectionSelectedSections] = useState({});
  const [noteCorrectionSelectedBatches, setNoteCorrectionSelectedBatches] = useState({});
  const [noteCorrectionPackages, setNoteCorrectionPackages] = useState({});
  const [copiedNoteCorrectionPackageKey, setCopiedNoteCorrectionPackageKey] = useState("");
  const [noteCorrectionReviewTexts, setNoteCorrectionReviewTexts] = useState({});
  const [noteCorrectionReviewValidations, setNoteCorrectionReviewValidations] = useState({});
  const [noteCorrectionReviewFilters, setNoteCorrectionReviewFilters] = useState({});
  const [noteCorrectionSaveStates, setNoteCorrectionSaveStates] = useState({});
  const [noteCorrectionSaveReadinessStates, setNoteCorrectionSaveReadinessStates] = useState({});
  const [chapterWorkspaceStates, setChapterWorkspaceStates] = useState({});
  const [noteClassificationPackages, setNoteClassificationPackages] = useState({});
  const [copiedNoteClassificationPackageKey, setCopiedNoteClassificationPackageKey] = useState("");
  const [noteClassificationReviewTexts, setNoteClassificationReviewTexts] = useState({});
  const [noteClassificationReviewValidations, setNoteClassificationReviewValidations] = useState({});
  const [triSourceObjectPackages, setTriSourceObjectPackages] = useState({});
  const [relationCandidateDryRunPackages, setRelationCandidateDryRunPackages] = useState({});
  const [objectCandidateHumanReviewWorkbenches, setObjectCandidateHumanReviewWorkbenches] = useState({});
  const [objectCandidateHumanReviewValidations, setObjectCandidateHumanReviewValidations] = useState({});

  if (!book) {
    return <StateMessage title="书籍详情暂不可用" body="该书籍的章节信息尚未返回。" />;
  }

  const chapters = book.chapters || [];
  const progress = book.object_import_progress || book.progress || {};
  const nextChapter = progress.next_chapter || null;
  const activeChapterId = selectedChapterId || nextChapter?.chapter_id || chapters[0]?.chapter_id || null;
  const activeChapter = chapters.find((chapter) => chapter.chapter_id === Number(activeChapterId)) || nextChapter;
  const totalCount = progress.total_count ?? chapters.length;
  const completedCount = progress.completed_count ?? 0;
  const typeLabel = chapteredDocumentTypeLabel(book.document_type, book.object_import_mode);

  useEffect(() => {
    const requestedChapterId = Number(initialChapterId || 0);
    if (requestedChapterId && chapters.some((chapter) => Number(chapter.chapter_id) === requestedChapterId)) {
      setSelectedChapterId(requestedChapterId);
    }
    if (initialWorkflow === "notes-import") {
      setShowProcessingWorkspace(true);
    }
  }, [initialChapterId, initialWorkflow, book?.document_id]);

  useEffect(() => {
    if (!book?.document_id || !activeChapter?.chapter_id) return;
    loadChapterWorkspaceState(activeChapter);
  }, [book?.document_id, activeChapter?.chapter_id]);

  async function loadChapterWorkspaceState(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setChapterWorkspaceStates((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/workspace-state`
      );
      setChapterWorkspaceStates((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setChapterWorkspaceStates((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: apiErrorMessage(error, "workspace state 暂不可用。") },
      }));
    }
  }

  async function runChapterZoteroNotesDryRun(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setZoteroDryRuns((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/zotero-notes/dry-run`);
      setZoteroDryRuns((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setZoteroDryRuns((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "Zotero notes 只读检查暂不可用。" },
      }));
    }
  }

  function setChapterZoteroImportConfirmation(chapter, checked) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setZoteroImportConfirmations((state) => ({ ...state, [key]: !!checked }));
  }

  async function confirmChapterZoteroNotesImport(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    const dryRun = zoteroDryRuns[key]?.data || {};
    setZoteroApplyStates((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await postJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/zotero-notes/apply`,
        {
          confirm_write: true,
          confirmation_context: CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT,
          document_id: book.document_id,
          chapter_id: chapter.chapter_id,
          zotero_item_key: dryRun.zotero_item_key,
          zotero_attachment_key: dryRun.zotero_attachment_key,
          expected_would_insert_count: Number(dryRun.would_insert_count ?? 0),
        }
      );
      setZoteroApplyStates((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setZoteroApplyStates((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "Zotero notes 导入请求被阻断或暂不可用。" },
      }));
    }
  }

  async function loadNoteCorrectionReviewPlan(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setNoteCorrectionPlans((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/note-correction-review-plan`);
      setNoteCorrectionPlans((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
      setNoteCorrectionModes((state) => ({
        ...state,
        [key]: state[key] || payload.recommended_mode || "full_chapter",
      }));
      setNoteCorrectionBatchSizes((state) => ({ ...state, [key]: state[key] || 15 }));
      setNoteCorrectionSelectedSections((state) => ({
        ...state,
        [key]: state[key] || payload.sections?.[0]?.section_id || "",
      }));
    } catch (error) {
      setNoteCorrectionPlans((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "笔记纠错审核方式推荐暂不可用。" },
      }));
    }
  }

  function selectNoteCorrectionMode(chapter, mode) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setNoteCorrectionModes((state) => ({ ...state, [key]: mode }));
  }

  function selectNoteCorrectionSection(chapter, sectionId) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setNoteCorrectionSelectedSections((state) => ({ ...state, [key]: sectionId }));
  }

  function selectNoteCorrectionBatchSize(chapter, batchSize) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setNoteCorrectionBatchSizes((state) => ({ ...state, [key]: Number(batchSize) }));
    setNoteCorrectionSelectedBatches((state) => ({ ...state, [key]: 0 }));
  }

  function selectNoteCorrectionBatch(chapter, batchIndex) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setNoteCorrectionSelectedBatches((state) => ({ ...state, [key]: Number(batchIndex) }));
  }

  async function previewNoteCorrectionPackage(chapter, scope = null) {
    if (!chapter?.chapter_id) return;
    const key = noteCorrectionScopeKey(chapter, scope);
    setNoteCorrectionPackages((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(noteCorrectionPackageUrl(book.document_id, chapter.chapter_id, scope));
      setNoteCorrectionPackages((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setNoteCorrectionPackages((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "笔记纠错包只读预览暂不可用。" },
      }));
    }
  }

  async function copyNoteCorrectionPackage(chapter, scope = null) {
    const key = noteCorrectionScopeKey(chapter, scope);
    const payload = noteCorrectionPackages[key]?.data;
    if (!payload) return;
    const text = payload.copy_ready_prompt || buildNoteCorrectionCopyPrompt(payload);
    await copyTextToClipboard(text);
    setCopiedNoteCorrectionPackageKey(key);
  }

  function updateNoteCorrectionReviewText(chapter, text, scope = null) {
    const key = noteCorrectionScopeKey(chapter, scope);
    if (!key) return;
    setNoteCorrectionReviewTexts((state) => ({ ...state, [key]: text }));
    setNoteCorrectionReviewValidations((state) => ({
      ...state,
      [key]: { status: "idle", data: state[key]?.data || null, error: "" },
    }));
    setNoteCorrectionSaveStates((state) => ({
      ...state,
      [key]: { status: "idle", data: state[key]?.data || null, error: "" },
    }));
  }

  async function validateNoteCorrectionReview(chapter, scope = null) {
    if (!chapter?.chapter_id) return;
    const key = noteCorrectionScopeKey(chapter, scope);
    const jsonText = noteCorrectionReviewTexts[key] || "";
    setNoteCorrectionReviewValidations((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const route = noteCorrectionValidateRoute(book.document_id, chapter.chapter_id, scope);
      const body = noteCorrectionValidateBody(jsonText, scope);
      const payload = await postJson(
        route,
        body
      );
      setNoteCorrectionReviewValidations((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setNoteCorrectionReviewValidations((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "返回 JSON 校验暂不可用。" },
      }));
    }
  }

  function setNoteCorrectionReviewFilter(chapter, filter, scope = null) {
    const key = noteCorrectionScopeKey(chapter, scope);
    if (!key) return;
    setNoteCorrectionReviewFilters((state) => ({ ...state, [key]: filter }));
  }

  async function loadNoteCorrectionSaveReadiness(chapter, scope = null) {
    if (!chapter?.chapter_id) return;
    const key = noteCorrectionScopeKey(chapter, scope);
    if (!key) return;
    setNoteCorrectionSaveReadinessStates((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/note-correction-review/save-readiness`
      );
      setNoteCorrectionSaveReadinessStates((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setNoteCorrectionSaveReadinessStates((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: apiErrorMessage(error, "保存 readiness 检查暂不可用。") },
      }));
    }
  }

  async function saveNoteCorrectionReview(chapter, scope = null, payload = {}) {
    if (!chapter?.chapter_id) return;
    const key = noteCorrectionScopeKey(chapter, scope);
    setNoteCorrectionSaveStates((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const response = await postJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/note-correction-review/save`,
        payload
      );
      setNoteCorrectionSaveStates((state) => ({
        ...state,
        [key]: { status: "ready", data: response, error: "" },
      }));
      if (response?.status === "saved") {
        await loadChapterWorkspaceState(chapter);
      }
    } catch (error) {
      setNoteCorrectionSaveStates((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: apiErrorMessage(error, "保存人工审计结果被阻断或暂不可用。") },
      }));
    }
  }

  async function previewNoteClassificationPackage(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setNoteClassificationPackages((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/note-classification/dry-run-package`);
      setNoteClassificationPackages((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setNoteClassificationPackages((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "笔记分类包只读检查暂不可用。" },
      }));
    }
  }

  async function copyNoteClassificationPackage(chapter) {
    const key = String(chapter?.chapter_id || "");
    const payload = noteClassificationPackages[key]?.data;
    if (!payload?.ready) return;
    const text = buildNoteClassificationCopyPrompt(payload);
    await copyTextToClipboard(text);
    setCopiedNoteClassificationPackageKey(key);
  }

  function updateNoteClassificationReviewText(chapter, text) {
    const key = String(chapter?.chapter_id || "");
    if (!key) return;
    setNoteClassificationReviewTexts((state) => ({ ...state, [key]: text }));
    setNoteClassificationReviewValidations((state) => ({
      ...state,
      [key]: { status: "idle", data: state[key]?.data || null, error: "" },
    }));
  }

  async function validateNoteClassificationReview(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    const jsonText = noteClassificationReviewTexts[key] || "";
    setNoteClassificationReviewValidations((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await postJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/note-classification/validate-manual-json`,
        { json_text: jsonText }
      );
      setNoteClassificationReviewValidations((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setNoteClassificationReviewValidations((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "分类返回 JSON 校验暂不可用。" },
      }));
    }
  }

  async function previewTriSourceObjectPackage(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setTriSourceObjectPackages((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/object-candidates/dry-run`);
      setTriSourceObjectPackages((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setTriSourceObjectPackages((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "三路对象候选包 dry-run 暂不可用。" },
      }));
    }
  }

  async function previewRelationCandidateDryRun(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setRelationCandidateDryRunPackages((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/relation-candidates/dry-run`);
      setRelationCandidateDryRunPackages((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setRelationCandidateDryRunPackages((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "关系候选 dry-run 暂不可用。" },
      }));
    }
  }

  async function loadObjectCandidateHumanReviewWorkbench(chapter) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setObjectCandidateHumanReviewWorkbenches((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await getJson(`/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/object-candidates/review-workbench`);
      setObjectCandidateHumanReviewWorkbenches((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setObjectCandidateHumanReviewWorkbenches((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: "对象候选人工审核工作台暂不可用。" },
      }));
    }
  }

  async function validateObjectCandidateHumanReview(chapter, reviewPayload) {
    if (!chapter?.chapter_id) return;
    const key = String(chapter.chapter_id);
    setObjectCandidateHumanReviewValidations((state) => ({
      ...state,
      [key]: { status: "loading", data: state[key]?.data || null, error: "" },
    }));
    try {
      const payload = await postJson(
        `/api/v1/library/books/${book.document_id}/chapters/${chapter.chapter_id}/object-candidates/validate-human-review`,
        { human_review: reviewPayload }
      );
      setObjectCandidateHumanReviewValidations((state) => ({
        ...state,
        [key]: { status: "ready", data: payload, error: "" },
      }));
    } catch (error) {
      setObjectCandidateHumanReviewValidations((state) => ({
        ...state,
        [key]: { status: "error", data: null, error: apiErrorMessage(error, "对象候选人工审核校验暂不可用。") },
      }));
    }
  }

  const chapterSummary = useMemo(() => {
    return chapters.reduce(
      (summary, chapter) => ({
        evidenceCount: summary.evidenceCount + Number(chapter.evidence_count || 0),
        objectCount: summary.objectCount + Number(chapter.object_count || 0),
        syncedNoteCount: summary.syncedNoteCount + Number(chapter.synced_note_count || chapter.annotation_count || 0),
        userNoteCount: summary.userNoteCount + Number(chapter.user_note_count || 0),
        evidenceOnlyCount: summary.evidenceOnlyCount + Number(chapter.evidence_only_count || 0),
      }),
      { evidenceCount: 0, objectCount: 0, syncedNoteCount: 0, userNoteCount: 0, evidenceOnlyCount: 0 }
    );
  }, [chapters]);

  return (
    <section className="detailStack bookDetailPage">
      <button className="detailBack" type="button" onClick={onBack}>
        返回已读书架
      </button>

      <article className="documentHero bookHero">
        <div className="documentHeroMain">
          <div className="cardMeta">
            <span>{typeLabel}</span>
            <span>{completedCount} / {totalCount} 章</span>
            <span>{chapterSummary.objectCount} 个对象</span>
            <span>{chapterSummary.evidenceCount} 条证据</span>
          </div>
          <h3>{book.title}</h3>
          <p>按章节组织正文证据、Zotero 笔记、对象审核与机制审核进度。</p>
        </div>
        <div className="documentHeroActions">
          <button
            className="quietButton"
            type="button"
            onClick={() => onOpenWorkspace?.(book.document_id, activeChapter?.chapter_id)}
          >
            打开 Research Workspace
          </button>
          <button className="primaryButton" type="button" onClick={() => setShowProcessingWorkspace((value) => !value)}>
            {showProcessingWorkspace ? "收起 Notes Import Flow" : "打开 Notes Import Flow"}
          </button>
          <span className="bookAdvancedWorkflowLabel">Advanced Workflow · 旧 Notes Import Linear Flow 保留</span>
        </div>
      </article>

      {showProcessingWorkspace && (
        <BookNoteFirstWorkspace
          book={book}
          chapters={chapters}
          activeChapter={activeChapter}
          activeChapterId={activeChapterId}
          setSelectedChapterId={setSelectedChapterId}
          dryRunState={zoteroDryRuns[String(activeChapter?.chapter_id || "")]}
          zoteroApplyState={zoteroApplyStates[String(activeChapter?.chapter_id || "")]}
          zoteroImportConfirmed={!!zoteroImportConfirmations[String(activeChapter?.chapter_id || "")]}
          onRunDryRun={() => runChapterZoteroNotesDryRun(activeChapter)}
          onZoteroImportConfirmationChange={(checked) => setChapterZoteroImportConfirmation(activeChapter, checked)}
          onConfirmZoteroNotesImport={() => confirmChapterZoteroNotesImport(activeChapter)}
          noteCorrectionPlanState={noteCorrectionPlans[String(activeChapter?.chapter_id || "")]}
          noteCorrectionMode={noteCorrectionModes[String(activeChapter?.chapter_id || "")]}
          noteCorrectionBatchSize={noteCorrectionBatchSizes[String(activeChapter?.chapter_id || "")] || 15}
          noteCorrectionSelectedSection={noteCorrectionSelectedSections[String(activeChapter?.chapter_id || "")] || ""}
          noteCorrectionSelectedBatch={noteCorrectionSelectedBatches[String(activeChapter?.chapter_id || "")] || 0}
          noteCorrectionPackageStates={noteCorrectionPackages}
          copiedNoteCorrectionPackageKey={copiedNoteCorrectionPackageKey}
          noteCorrectionReviewTexts={noteCorrectionReviewTexts}
          noteCorrectionReviewValidationStates={noteCorrectionReviewValidations}
          noteCorrectionReviewFilters={noteCorrectionReviewFilters}
          noteCorrectionSaveStates={noteCorrectionSaveStates}
          noteCorrectionSaveReadinessStates={noteCorrectionSaveReadinessStates}
          workspaceState={chapterWorkspaceStates[String(activeChapter?.chapter_id || "")]}
          onLoadNoteCorrectionPlan={() => loadNoteCorrectionReviewPlan(activeChapter)}
          onSelectNoteCorrectionMode={(mode) => selectNoteCorrectionMode(activeChapter, mode)}
          onSelectNoteCorrectionSection={(sectionId) => selectNoteCorrectionSection(activeChapter, sectionId)}
          onSelectNoteCorrectionBatchSize={(batchSize) => selectNoteCorrectionBatchSize(activeChapter, batchSize)}
          onSelectNoteCorrectionBatch={(batchIndex) => selectNoteCorrectionBatch(activeChapter, batchIndex)}
          onPreviewNoteCorrectionPackage={(scope) => previewNoteCorrectionPackage(activeChapter, scope)}
          onCopyNoteCorrectionPackage={(scope) => copyNoteCorrectionPackage(activeChapter, scope)}
          onNoteCorrectionReviewTextChange={(text, scope) => updateNoteCorrectionReviewText(activeChapter, text, scope)}
          onValidateNoteCorrectionReview={(scope) => validateNoteCorrectionReview(activeChapter, scope)}
          onSetNoteCorrectionReviewFilter={(filter, scope) => setNoteCorrectionReviewFilter(activeChapter, filter, scope)}
          onLoadNoteCorrectionSaveReadiness={(scope) => loadNoteCorrectionSaveReadiness(activeChapter, scope)}
          onSaveNoteCorrectionReview={(scope, payload) => saveNoteCorrectionReview(activeChapter, scope, payload)}
          noteClassificationPackageState={noteClassificationPackages[String(activeChapter?.chapter_id || "")]}
          copiedNoteClassificationPackage={copiedNoteClassificationPackageKey === String(activeChapter?.chapter_id || "")}
          noteClassificationReviewText={noteClassificationReviewTexts[String(activeChapter?.chapter_id || "")] || ""}
          noteClassificationReviewValidationState={noteClassificationReviewValidations[String(activeChapter?.chapter_id || "")]}
          onPreviewNoteClassificationPackage={() => previewNoteClassificationPackage(activeChapter)}
          onCopyNoteClassificationPackage={() => copyNoteClassificationPackage(activeChapter)}
          onNoteClassificationReviewTextChange={(text) => updateNoteClassificationReviewText(activeChapter, text)}
          onValidateNoteClassificationReview={() => validateNoteClassificationReview(activeChapter)}
          triSourceObjectPackageState={triSourceObjectPackages[String(activeChapter?.chapter_id || "")]}
          onPreviewTriSourceObjectPackage={() => previewTriSourceObjectPackage(activeChapter)}
          relationCandidateDryRunPackageState={relationCandidateDryRunPackages[String(activeChapter?.chapter_id || "")]}
          onPreviewRelationCandidateDryRun={() => previewRelationCandidateDryRun(activeChapter)}
          objectCandidateHumanReviewWorkbenchState={objectCandidateHumanReviewWorkbenches[String(activeChapter?.chapter_id || "")]}
          objectCandidateHumanReviewValidationState={objectCandidateHumanReviewValidations[String(activeChapter?.chapter_id || "")]}
          onLoadObjectCandidateHumanReviewWorkbench={() => loadObjectCandidateHumanReviewWorkbench(activeChapter)}
          onValidateObjectCandidateHumanReview={(reviewPayload) => validateObjectCandidateHumanReview(activeChapter, reviewPayload)}
        />
      )}

      <BookUnitProcessingPanel
        chapters={chapters}
        dryRuns={zoteroDryRuns}
        onRunDryRun={runChapterZoteroNotesDryRun}
      />

      <section className="bookProgressPanel">
        <div className="sectionHeader">
          <h3>章节处理进度</h3>
          <span>{progress.done ? "已完成" : "进行中"}</span>
        </div>
        <div className="bookProgressGrid">
          <Metric label="总章节" value={totalCount} />
          <Metric label="已完成" value={completedCount} />
          <Metric label="未开始" value={progress.not_started_count ?? 0} />
          <Metric label="证据片段" value={chapterSummary.evidenceCount} />
          <Metric label="已同步 notes" value={chapterSummary.syncedNoteCount} />
          <Metric label="用户笔记" value={chapterSummary.userNoteCount} />
        </div>
      </section>

      <section className="bookChapterSection">
        <div className="sectionHeader">
          <h3>章节列表</h3>
          <span>{chapters.length} 章</span>
        </div>
        <div className="bookChapterList">
          {chapters.map((chapter) => {
            const expanded = expandedChapterId === chapter.chapter_id;
            const chapterKey = chapterListKey(book.document_id, chapter);
            return (
              <article key={chapterKey} className="bookChapterCard">
                <button
                  className="bookChapterSummary"
                  type="button"
                  onClick={() => setExpandedChapterId(expanded ? null : chapter.chapter_id)}
                  aria-expanded={expanded}
                >
                  <span className="bookChapterTitle">{chapterTitle(chapter)}</span>
                  <span className="bookChapterMeta">
                    {pageRange(chapter)} · {chapterStatusLabel(chapter.object_import_status)} · {chapter.user_note_count || 0} 用户笔记 · {chapter.object_count || 0} 对象 · {chapter.evidence_count || 0} 证据
                  </span>
                </button>
                {expanded && (
                  <div className="bookChapterBody">
                    <div className="bookChapterStats">
                      <Metric label="对象" value={chapter.object_count || 0} />
                      <Metric label="证据" value={chapter.evidence_count || 0} />
                      <Metric label="页码" value={pageRange(chapter)} />
                    </div>
                    {Number(chapter.object_count || 0) === 0 ? (
                      <StateMessage
                        title="本章尚无已审核对象"
                        body="三路对象候选仍是 planned / not_implemented；本阶段只做笔记纠错 round-trip，不生成对象候选。"
                      />
                    ) : (
                      <p className="subtlePlaceholder">本章对象会在对象审核完成后进入机制候选入口。</p>
                    )}
                    <ChapterZoteroDryRunCard
                      chapter={chapter}
                      dryRunState={zoteroDryRuns[String(chapter.chapter_id)]}
                      onRun={() => runChapterZoteroNotesDryRun(chapter)}
                    />
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </section>
  );
}

import ImportCommitStatus from "../features/importing/review/ImportCommitStatus.jsx";
import ReviewInputPanel from "../features/importing/review/ReviewInputPanel.jsx";
import ReviewObjectList from "../features/importing/review/ReviewObjectList.jsx";
import {
  buildReviewedPackage,
  buildReviewItems,
} from "../features/importing/review/reviewModel.js";
import {
  buildSuggestionUploadPayload,
} from "../features/importing/review/reviewApi.js";
import { reviewApi } from "../features/importing/review/reviewApiClient.js";
import {
  addEvidenceRef,
  addReviewTag,
  editEvidenceField,
  editReviewTag,
  removeEvidenceRef,
  removeReviewTag,
  selectEvidenceSection,
  toggleReviewStatus,
  updateReviewComment,
} from "../features/importing/review/reviewState.js";
import {
  continueCommitAfterRemap,
  runCommitPipeline,
} from "../features/importing/review/commitPipeline.js";

export default function ImportReviewPage({
  state,
  setState,
  updateSafety,
  onNavigate,
  onRefreshReadShelf,
}) {
  async function fetchSourceTraceSections(jobId) {
    try {
      const data = await reviewApi.fetchSourceTraceSections(jobId);
      setState(current => ({ ...current, sourceTraceSections: data.sections || [] }));
    } catch (_) {
      setState(current => ({ ...current, sourceTraceSections: [] }));
    }
  }

  async function uploadPastedJson() {
    const jobId = state.jobId.trim();
    const raw = state.jsonPaste.trim();
    if (!jobId) {
      setState(current => ({ ...current, uploadError: "请输入 import_job_id。" }));
      return;
    }
    if (!raw) {
      setState(current => ({ ...current, uploadError: "请粘贴 ChatGPT 输出的 JSON。" }));
      return;
    }

    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      setState(current => ({ ...current, uploadError: `JSON 解析失败：${error.message}` }));
      return;
    }
    if (!parsed.objects || !Array.isArray(parsed.objects)) {
      setState(current => ({ ...current, uploadError: "JSON 缺少 objects 数组。" }));
      return;
    }

    setState(current => ({
      ...current,
      uploadLoading: true,
      uploadError: "",
      uploadResult: null,
      suggestions: null,
      reviewItems: [],
      reviewSource: "",
    }));
    try {
      const result = await reviewApi.uploadSuggestions(
        jobId,
        buildSuggestionUploadPayload(parsed),
      );
      const reviewItems = buildReviewItems(parsed.objects, "uploaded_suggestions");
      setState(current => ({
        ...current,
        uploadLoading: false,
        uploadResult: result,
        uploadError: "",
        suggestions: {
          status: "ok",
          suggestions_status: "ai_suggested",
          object_count: reviewItems.length,
        },
        reviewItems,
        reviewSource: "ai_suggestions",
        saveResult: null,
      }));
      updateSafety(result);
      fetchSourceTraceSections(jobId);
    } catch (error) {
      setState(current => ({
        ...current,
        uploadLoading: false,
        uploadError: `上传失败：${error.message}`,
      }));
    }
  }

  async function loadSuggestions() {
    const jobId = state.jobId.trim();
    if (!jobId) return;
    startLoadingReviewItems();
    try {
      const data = await reviewApi.fetchSuggestions(jobId);
      if (data.status === "not_found") {
        setState(current => ({
          ...current,
          suggestionsLoading: false,
          suggestionsError: "该导入作业尚未上传 AI 建议，请先粘贴 JSON 上传。",
        }));
        return;
      }
      finishLoadingReviewItems(data, buildReviewItems(data.objects || [], "ai_suggestions"), "ai_suggestions");
      updateSafety(data);
      fetchSourceTraceSections(jobId);
    } catch (error) {
      failLoadingReviewItems(error);
    }
  }

  async function loadReviewedObjects() {
    const jobId = state.jobId.trim();
    if (!jobId) return;
    startLoadingReviewItems();
    try {
      const data = await reviewApi.fetchReviewedObjects(jobId);
      if (data.status === "not_found") {
        setState(current => ({
          ...current,
          suggestionsLoading: false,
          suggestionsError: "该导入作业尚未保存审核结果。",
        }));
        return;
      }
      finishLoadingReviewItems(data, buildReviewItems(data.objects || [], "reviewed_objects"), "reviewed_objects");
      updateSafety(data);
      fetchSourceTraceSections(jobId);
    } catch (error) {
      failLoadingReviewItems(error);
    }
  }

  function startLoadingReviewItems() {
    setState(current => ({
      ...current,
      suggestionsLoading: true,
      suggestionsError: "",
      suggestions: null,
      reviewItems: [],
      reviewSource: "",
      saveResult: null,
    }));
  }

  function finishLoadingReviewItems(data, reviewItems, reviewSource) {
    setState(current => ({
      ...current,
      suggestions: data,
      suggestionsLoading: false,
      suggestionsError: "",
      reviewItems,
      reviewSource,
      uploadResult: null,
      saveResult: null,
    }));
  }

  function failLoadingReviewItems(error) {
    setState(current => ({
      ...current,
      suggestionsLoading: false,
      suggestionsError: `加载失败：${error.message}`,
    }));
  }

  async function saveReview() {
    const jobId = state.jobId.trim();
    if (!jobId) return;
    setState(current => ({ ...current, saveStatus: "saving" }));
    try {
      const result = await reviewApi.saveReviewedObjects(
        jobId,
        buildReviewedPackage(state.reviewItems),
      );
      setState(current => ({ ...current, saveStatus: "saved", saveResult: result }));
      updateSafety(result);
    } catch (error) {
      setState(current => ({
        ...current,
        saveStatus: "error",
        saveResult: { error: error.message },
      }));
    }
  }

  async function loadRemapPreview() {
    const jobId = state.jobId.trim();
    if (!jobId) return;
    setState(current => ({ ...current, remapLoading: true, remapPreview: null }));
    try {
      const data = await reviewApi.previewReviewedObjectRemap(jobId);
      setState(current => ({ ...current, remapLoading: false, remapPreview: data }));
      updateSafety(data);
    } catch (error) {
      setState(current => ({
        ...current,
        remapLoading: false,
        remapPreview: { error: error.message },
      }));
    }
  }

  function commitFullPipeline() {
    return runCommitPipeline({
      jobId: state.jobId,
      api: reviewApi,
      setState,
      updateSafety,
      onRefresh: onRefreshReadShelf,
    });
  }

  function continueFromRemap() {
    const jobId = state.jobId.trim();
    if (!jobId) return;
    return continueCommitAfterRemap({
      jobId,
      api: reviewApi,
      setState,
      updateSafety,
      onRefresh: onRefreshReadShelf,
    });
  }

  const handlers = {
    toggleReviewStatus: (index, status) => setState(current => toggleReviewStatus(current, index, status)),
    editTag: (index, layer, tagIndex, value) => setState(current => editReviewTag(current, index, layer, tagIndex, value)),
    removeTag: (index, layer, tagIndex) => setState(current => removeReviewTag(current, index, layer, tagIndex)),
    addTag: (index, layer) => setState(current => addReviewTag(current, index, layer)),
    setUserComment: (index, value) => setState(current => updateReviewComment(current, index, value)),
    editEvidenceField: (index, refIndex, field, value) => setState(current => editEvidenceField(current, index, refIndex, field, value)),
    removeEvidenceRef: (index, refIndex) => setState(current => removeEvidenceRef(current, index, refIndex)),
    addEvidenceRef: index => setState(current => addEvidenceRef(current, index)),
    selectSectionForRef: (index, refIndex, sectionId) => setState(current => selectEvidenceSection(current, index, refIndex, sectionId)),
  };

  const commitPhase = state.commitPhase || {};
  const pipelineRunning = commitPhase.paper?.status === "running"
    || commitPhase.remap?.status === "running"
    || commitPhase.objects?.status === "running";
  const hasReviewItems = state.reviewItems.length > 0;

  return (
    <section className="importReviewPage">
      <div className="importReviewBanner">
        <p><strong>这些对象由 ChatGPT 生成，尚未进入资料库。</strong></p>
        <p>保存审核结果只写入 staging，不写核心数据库。最终载入资料库将在后续 Import Commit 阶段完成。</p>
      </div>

      <ReviewInputPanel
        jobId={state.jobId}
        jsonPaste={state.jsonPaste}
        uploadLoading={state.uploadLoading}
        uploadError={state.uploadError}
        uploadResult={state.uploadResult}
        suggestionsLoading={state.suggestionsLoading}
        suggestionsError={state.suggestionsError}
        onJobIdChange={value => setState(current => ({ ...current, jobId: value }))}
        onJsonPasteChange={value => setState(current => ({ ...current, jsonPaste: value }))}
        onUpload={uploadPastedJson}
        onLoadSuggestions={loadSuggestions}
        onLoadReviewedObjects={loadReviewedObjects}
      />

      <ReviewObjectList
        reviewItems={state.reviewItems}
        reviewSource={state.reviewSource}
        sourceTraceSections={state.sourceTraceSections}
        handlers={handlers}
      />

      <ImportCommitStatus
        visible={hasReviewItems}
        saveStatus={state.saveStatus}
        saveResult={state.saveResult}
        remapPreview={state.remapPreview}
        remapLoading={state.remapLoading}
        commitLoading={state.commitLoading}
        commitPhase={commitPhase}
        confirmRemapFailed={state.confirmRemapFailed}
        pipelineRunning={pipelineRunning}
        onSave={saveReview}
        onCommit={commitFullPipeline}
        onPreviewRemap={loadRemapPreview}
        onContinueRemap={continueFromRemap}
        onCancelRemap={() => setState(current => ({ ...current, confirmRemapFailed: false }))}
        onNavigate={onNavigate}
      />
    </section>
  );
}

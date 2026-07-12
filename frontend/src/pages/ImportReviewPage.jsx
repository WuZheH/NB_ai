import { getJson, postJson } from "../api/client.js";
import StateMessage from "../components/StateMessage.jsx";
import ReviewObjectCard from "../components/ReviewObjectCard.jsx";

export default function ImportReviewPage({ state, setState, updateSafety, onNavigate, onRefreshReadShelf }) {

  async function fetchSourceTraceSections(jid) {
    try {
      const data = await getJson(`/api/v1/imports/${jid}/source-trace-sections`);
      setState(s => ({ ...s, sourceTraceSections: data.sections || [] }));
    } catch (_) {
      setState(s => ({ ...s, sourceTraceSections: [] }));
    }
  }

  async function uploadPastedJson() {
    const jid = state.jobId.trim();
    const raw = state.jsonPaste.trim();
    if (!jid) { setState(s => ({ ...s, uploadError: "请输入 import_job_id。" })); return; }
    if (!raw) { setState(s => ({ ...s, uploadError: "请粘贴 ChatGPT 输出的 JSON。" })); return; }
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) {
      setState(s => ({ ...s, uploadError: `JSON 解析失败：${e.message}` }));
      return;
    }
    if (!parsed.objects || !Array.isArray(parsed.objects)) {
      setState(s => ({ ...s, uploadError: "JSON 缺少 objects 数组。" }));
      return;
    }
    setState(s => ({ ...s, uploadLoading: true, uploadError: "", uploadResult: null, suggestions: null, reviewItems: [], reviewSource: "" }));
    try {
      const result = await postJson(`/api/v1/imports/${jid}/ai-suggestions`, {
        schema_version: parsed.schema_version || "object_tag_suggestions_v1",
        created_by: parsed.created_by || "external_chatgpt_user_pasted",
        objects: parsed.objects,
      });
      setState(s => ({ ...s, uploadLoading: false, uploadResult: result, uploadError: "" }));
      updateSafety(result);
      // Auto-load the full suggestions and build review items
      const objects = parsed.objects.map(obj => ({
        ...obj,
        reviewStatus: "suggested",
        editedTags: {
          topic_tags: [...(obj.topic_tags || [])],
          problem_tags: [...(obj.problem_tags || [])],
          mechanism_tags: [...(obj.mechanism_tags || [])],
          inspiration_tags: [...(obj.inspiration_tags || [])],
        },
        editedEvidenceRefs: (obj.evidence_refs || []).map(r => ({ ...r })),
        userComment: "",
      }));
      setState(s => ({
        ...s,
        uploadLoading: false,
        uploadResult: result,
        uploadError: "",
        suggestions: { status: "ok", suggestions_status: "ai_suggested", object_count: objects.length },
        reviewItems: objects,
        reviewSource: "ai_suggestions",
        saveResult: null,
      }));
      fetchSourceTraceSections(jid);
    } catch (e) {
      setState(s => ({ ...s, uploadLoading: false, uploadError: `上传失败：${e.message}` }));
    }
  }

  async function loadSuggestions() {
    const jid = state.jobId.trim();
    if (!jid) return;
    setState(s => ({ ...s, suggestionsLoading: true, suggestionsError: "", suggestions: null, reviewItems: [], reviewSource: "", saveResult: null }));
    try {
      const data = await getJson(`/api/v1/imports/${jid}/ai-suggestions`);
      if (data.status === "not_found") {
        setState(s => ({ ...s, suggestionsLoading: false, suggestionsError: "该导入作业尚未上传 AI 建议，请先粘贴 JSON 上传。" }));
        return;
      }
      const objects = (data.objects || []).length ? data.objects : [];
      const reviewItems = objects.map(obj => ({
        ...obj,
        reviewStatus: obj.status || "suggested",
        editedTags: {
          topic_tags: [...(obj.topic_tags || [])],
          problem_tags: [...(obj.problem_tags || [])],
          mechanism_tags: [...(obj.mechanism_tags || [])],
          inspiration_tags: [...(obj.inspiration_tags || [])],
        },
        editedEvidenceRefs: (obj.evidence_refs || []).map(r => ({ ...r })),
        userComment: obj.user_comment || "",
      }));
      setState(s => ({
        ...s,
        suggestions: data,
        suggestionsLoading: false,
        suggestionsError: "",
        reviewItems,
        reviewSource: "ai_suggestions",
        uploadResult: null,
        saveResult: null,
      }));
      updateSafety(data);
      fetchSourceTraceSections(jid);
    } catch (e) {
      setState(s => ({ ...s, suggestionsLoading: false, suggestionsError: `加载失败：${e.message}` }));
    }
  }

  async function loadReviewedObjects() {
    const jid = state.jobId.trim();
    if (!jid) return;
    setState(s => ({ ...s, suggestionsLoading: true, suggestionsError: "", suggestions: null, reviewItems: [], reviewSource: "", saveResult: null }));
    try {
      const data = await getJson(`/api/v1/imports/${jid}/reviewed-objects`);
      if (data.status === "not_found") {
        setState(s => ({ ...s, suggestionsLoading: false, suggestionsError: "该导入作业尚未保存审核结果。" }));
        return;
      }
      const objects = (data.objects || []).length ? data.objects : [];
      const reviewItems = objects.map(obj => ({
        ...obj,
        reviewStatus: obj.review_status || "suggested",
        editedTags: {
          topic_tags: (obj.topic_tags || []).map(t => typeof t === "string" ? t : (t.tag || "")),
          problem_tags: (obj.problem_tags || []).map(t => typeof t === "string" ? t : (t.tag || "")),
          mechanism_tags: (obj.mechanism_tags || []).map(t => typeof t === "string" ? t : (t.tag || "")),
          inspiration_tags: (obj.inspiration_tags || []).map(t => typeof t === "string" ? t : (t.tag || "")),
        },
        editedEvidenceRefs: (obj.evidence_refs || []).map(r => ({ ...r })),
        userComment: obj.user_comment || "",
      }));
      setState(s => ({
        ...s,
        suggestions: data,
        suggestionsLoading: false,
        suggestionsError: "",
        reviewItems,
        reviewSource: "reviewed_objects",
        uploadResult: null,
        saveResult: null,
      }));
      updateSafety(data);
      fetchSourceTraceSections(jid);
    } catch (e) {
      setState(s => ({ ...s, suggestionsLoading: false, suggestionsError: `加载失败：${e.message}` }));
    }
  }

  function toggleReviewStatus(index, status) {
    setState(s => {
      const items = [...s.reviewItems];
      items[index] = { ...items[index], reviewStatus: status };
      return { ...s, reviewItems: items };
    });
  }

  function editTag(index, layer, tagIdx, value) {
    setState(s => {
      const items = [...s.reviewItems];
      const tags = [...items[index].editedTags[layer]];
      if (tagIdx < tags.length) {
        tags[tagIdx] = value;
      }
      items[index] = { ...items[index], editedTags: { ...items[index].editedTags, [layer]: tags } };
      return { ...s, reviewItems: items };
    });
  }

  function removeTag(index, layer, tagIdx) {
    setState(s => {
      const items = [...s.reviewItems];
      const tags = [...items[index].editedTags[layer]];
      tags.splice(tagIdx, 1);
      items[index] = { ...items[index], editedTags: { ...items[index].editedTags, [layer]: tags } };
      return { ...s, reviewItems: items };
    });
  }

  function addTag(index, layer) {
    setState(s => {
      const items = [...s.reviewItems];
      const tags = [...items[index].editedTags[layer], ""];
      items[index] = { ...items[index], editedTags: { ...items[index].editedTags, [layer]: tags } };
      return { ...s, reviewItems: items };
    });
  }

  function setUserComment(index, value) {
    setState(s => {
      const items = [...s.reviewItems];
      items[index] = { ...items[index], userComment: value };
      return { ...s, reviewItems: items };
    });
  }

  // ---- Evidence refs editing ----
  function editEvidenceField(index, refIdx, field, value) {
    setState(s => {
      const items = [...s.reviewItems];
      const refs = [...items[index].editedEvidenceRefs];
      if (refIdx < refs.length) {
        refs[refIdx] = { ...refs[refIdx], [field]: value };
      }
      items[index] = { ...items[index], editedEvidenceRefs: refs };
      return { ...s, reviewItems: items };
    });
  }

  function removeEvidenceRef(index, refIdx) {
    setState(s => {
      const items = [...s.reviewItems];
      const refs = [...items[index].editedEvidenceRefs];
      refs.splice(refIdx, 1);
      items[index] = { ...items[index], editedEvidenceRefs: refs };
      return { ...s, reviewItems: items };
    });
  }

  function addEvidenceRef(index) {
    setState(s => {
      const items = [...s.reviewItems];
      items[index] = {
        ...items[index],
        editedEvidenceRefs: [
          ...items[index].editedEvidenceRefs,
          { pdf_page: "", section_id: "", section_title: "", quote_text_short: "", paper_md_anchor: "" },
        ],
      };
      return { ...s, reviewItems: items };
    });
  }

  function selectSectionForRef(index, refIdx, sectionId) {
    const sec = state.sourceTraceSections.find(s => s.section_id === sectionId);
    if (sec) {
      setState(s => {
        const items = [...s.reviewItems];
        const refs = [...items[index].editedEvidenceRefs];
        if (refIdx < refs.length) {
          refs[refIdx] = {
            ...refs[refIdx],
            section_id: sec.section_id,
            section_title: sec.title,
            pdf_page: sec.pdf_page != null ? String(sec.pdf_page) : refs[refIdx].pdf_page,
          };
        }
        items[index] = { ...items[index], editedEvidenceRefs: refs };
        return { ...s, reviewItems: items };
      });
    }
  }

  async function saveReview() {
    const jid = state.jobId.trim();
    if (!jid) return;
    const objects = state.reviewItems.map(item => ({
      object_key: item.object_key,
      object_name: item.object_name,
      object_type: item.object_type,
      review_status: item.reviewStatus,
      aliases: item.aliases || [],
      topic_tags: (item.editedTags.topic_tags || []).map(t => ({ tag: t, status: item.reviewStatus === "accepted" ? "accepted" : "suggested" })),
      problem_tags: (item.editedTags.problem_tags || []).map(t => ({ tag: t, status: item.reviewStatus === "accepted" ? "accepted" : "suggested" })),
      mechanism_tags: (item.editedTags.mechanism_tags || []).map(t => ({ tag: t, status: item.reviewStatus === "accepted" ? "accepted" : "suggested" })),
      inspiration_tags: (item.editedTags.inspiration_tags || []).map(t => ({ tag: t, status: "suggested" })),
      evidence_refs: item.editedEvidenceRefs || item.evidence_refs || [],
      user_comment: item.userComment || "",
      warnings: item.warnings || [],
    }));
    setState(s => ({ ...s, saveStatus: "saving" }));
    try {
      const result = await postJson(`/api/v1/imports/${jid}/reviewed-objects`, {
        schema_version: "reviewed_object_tag_package_v1",
        reviewed_by: "user",
        objects,
      });
      setState(s => ({ ...s, saveStatus: "saved", saveResult: result }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({ ...s, saveStatus: "error", saveResult: { error: e.message } }));
    }
  }

  async function loadRemapPreview() {
    const jid = state.jobId.trim();
    if (!jid) return;
    setState(s => ({ ...s, remapLoading: true, remapPreview: null }));
    try {
      const data = await postJson(`/api/v1/imports/${jid}/remap-reviewed-objects-preview`, {});
      setState(s => ({ ...s, remapLoading: false, remapPreview: data }));
      updateSafety(data);
    } catch (e) {
      setState(s => ({ ...s, remapLoading: false, remapPreview: { error: e.message } }));
    }
  }

  async function commitFullPipeline() {
    const jid = state.jobId.trim();
    if (!jid) {
      setState(s => ({ ...s, saveStatus: "error", saveResult: { error: "请输入 import_job_id。" } }));
      return;
    }

    // Reset phases and confirm state
    setState(s => ({
      ...s,
      commitLoading: true,
      commitPhase: {
        paper: { status: "pending" },
        remap: { status: "pending" },
        objects: { status: "pending" },
      },
      confirmRemapFailed: false,
      saveStatus: "",
      saveResult: null,
    }));

    // ---- Phase A: commit-paper ----
    setState(s => updatePhase(s, "paper", "running"));
    try {
      const paperResult = await postJson(`/api/v1/imports/${jid}/commit-paper`, {
        confirm_write: true,
        confirmation_context: "commit_paper_after_preview",
      });
      updateSafety(paperResult);
      if (paperResult.status === "committed" || paperResult.status === "ok") {
        setState(s => updatePhase(s, "paper", "ok", paperResult));
      } else if (paperResult.status === "already_committed") {
        setState(s => updatePhase(s, "paper", "already_committed", paperResult));
      } else {
        setState(s => ({ ...s, commitLoading: false, commitPhase: updatePhaseEntry(s.commitPhase, "paper", "error", paperResult) }));
        return;
      }
    } catch (e) {
      setState(s => ({ ...s, commitLoading: false, commitPhase: updatePhaseEntry(s.commitPhase, "paper", "error", { error: e.message }) }));
      return;
    }

    // ---- Phase B: remap-reviewed-objects-preview ----
    setState(s => updatePhase(s, "remap", "running"));
    try {
      const remapResult = await postJson(`/api/v1/imports/${jid}/remap-reviewed-objects-preview`, {});
      updateSafety(remapResult);
      const failedCount = remapResult.summary?.failed || 0;
      if (remapResult.status === "ok" && failedCount > 0) {
        setState(s => updatePhase(s, "remap", "warning", remapResult));
        // Store remap preview for display
        setState(s => ({ ...s, remapPreview: remapResult, commitLoading: false, confirmRemapFailed: true }));
        return; // Pause — wait for user confirmation
      } else if (remapResult.status === "ok") {
        setState(s => ({ ...s, remapPreview: remapResult }));
        setState(s => updatePhase(s, "remap", "ok", remapResult));
      } else {
        setState(s => ({ ...s, commitLoading: false, remapPreview: remapResult, commitPhase: updatePhaseEntry(s.commitPhase, "remap", "error", remapResult) }));
        return;
      }
    } catch (e) {
      setState(s => ({ ...s, commitLoading: false, commitPhase: updatePhaseEntry(s.commitPhase, "remap", "error", { error: e.message }) }));
      return;
    }

    // ---- Phase C: commit-reviewed-objects ----
    await doCommitReviewedObjects(jid);
  }

  async function continueFromRemap() {
    const jid = state.jobId.trim();
    if (!jid) return;
    setState(s => ({ ...s, commitLoading: true, confirmRemapFailed: false, saveStatus: "", saveResult: null }));
    await doCommitReviewedObjects(jid);
  }

  async function doCommitReviewedObjects(jid) {
    setState(s => updatePhase(s, "objects", "running"));
    try {
      const data = await postJson(`/api/v1/imports/${jid}/commit-reviewed-objects`, {
        confirm_write: true,
        confirmation_context: "commit_reviewed_objects_after_remap",
      });
      updateSafety(data);
      if (data.status === "committed" || data.status === "ok") {
        setState(s => ({
          ...s,
          commitLoading: false,
          saveResult: data,
          saveStatus: "committed",
          commitPhase: updatePhaseEntry(s.commitPhase, "objects", "ok", data),
        }));
      } else if (data.status === "already_committed") {
        setState(s => ({
          ...s,
          commitLoading: false,
          saveResult: data,
          saveStatus: "committed",
          commitPhase: updatePhaseEntry(s.commitPhase, "objects", "already_committed", data),
        }));
      } else {
        setState(s => ({
          ...s,
          commitLoading: false,
          saveResult: data,
          saveStatus: "error",
          commitPhase: updatePhaseEntry(s.commitPhase, "objects", "error", data),
        }));
        return;
      }
    } catch (e) {
      setState(s => ({
        ...s,
        commitLoading: false,
        saveResult: { error: e.message },
        saveStatus: "error",
        commitPhase: updatePhaseEntry(s.commitPhase, "objects", "error", { error: e.message }),
      }));
      return;
    }

    // ---- Phase D: Refresh ----
    if (onRefreshReadShelf) onRefreshReadShelf();
  }

  function updatePhase(s, phase, status, resultData = null) {
    return {
      ...s,
      commitPhase: updatePhaseEntry(s.commitPhase, phase, status, resultData),
    };
  }

  function updatePhaseEntry(phase, key, status, resultData) {
    return {
      ...phase,
      [key]: { status, ...(resultData ? { result: resultData } : {}) },
    };
  }

  async function commitReviewedObjects() {
    // Legacy wrapper — redirect to full pipeline
    await commitFullPipeline();
  }

  const { jobId, jsonPaste, uploadLoading, uploadError, uploadResult, suggestions, suggestionsLoading, suggestionsError, reviewItems, reviewSource, sourceTraceSections, saveStatus, saveResult, remapPreview, remapLoading, commitLoading, commitPhase = {}, confirmRemapFailed } = state;
  const hasReviewItems = reviewItems.length > 0;
  const pipelineRunning = commitPhase.paper?.status === "running" || commitPhase.remap?.status === "running" || commitPhase.objects?.status === "running";

  // ---- Phase indicator helpers ----
  function commitPhaseRowClass(phaseEntry = {}) {
    const base = "commitPhaseRow";
    const status = phaseEntry.status || "pending";
    return `${base} ${base}--${status}`;
  }
  function phaseIcon(phaseEntry = {}) {
    const status = phaseEntry.status || "pending";
    const icons = {
      pending: "○", running: "◌", ok: "✓", already_committed: "✓",
      warning: "⚠", error: "✗"
    };
    return <span className={`commitPhaseIcon commitPhaseIcon--${status}`}>{icons[status] || "○"}</span>;
  }
  function phaseLabel(phaseEntry = {}) {
    const status = phaseEntry.status || "pending";
    const labels = {
      pending: "等待中", running: "进行中...", ok: "已完成",
      already_committed: "已提交（无重复写入）", warning: "需确认", error: "失败"
    };
    return labels[status] || status;
  }

  function pipelinePhaseSummary(phase = {}) {
    const papers = phase.paper?.status || "pending";
    const remaps = phase.remap?.status || "pending";
    const objs = phase.objects?.status || "pending";
    const countStatus = (s) => (s === "ok" || s === "already_committed" || s === "warning") ? 1 : 0;
    const done = countStatus(papers) + countStatus(remaps) + countStatus(objs);
    return `${done}/3 阶段完成`;
  }

  return (
    <section className="importReviewPage">
      <div className="importReviewBanner">
        <p><strong>这些对象由 ChatGPT 生成，尚未进入资料库。</strong></p>
        <p>保存审核结果只写入 staging，不写核心数据库。最终载入资料库将在后续 Import Commit 阶段完成。</p>
      </div>

      <div className="importReviewJobInput">
        <input
          value={jobId}
          onChange={e => setState(s => ({ ...s, jobId: e.target.value }))}
          placeholder="输入 import_job_id..."
          aria-label="Import Job ID"
        />
      </div>

      {/* JSON Paste + Upload */}
      <div className="importReviewPasteSection">
        <h3>导入 ChatGPT 对象标签建议</h3>
        <textarea
          value={jsonPaste}
          onChange={e => setState(s => ({ ...s, jsonPaste: e.target.value }))}
          placeholder="粘贴 ChatGPT 输出的 object_tag_suggestions_v1 JSON..."
          rows={10}
          aria-label="ChatGPT JSON input"
        />
        <div className="importReviewPasteActions">
          <button type="button" onClick={uploadPastedJson} disabled={uploadLoading || !jobId.trim() || !jsonPaste.trim()}>
            {uploadLoading ? "上传中..." : "上传建议"}
          </button>
          <button type="button" onClick={loadSuggestions} disabled={suggestionsLoading || !jobId.trim()}>
            {suggestionsLoading ? "加载中..." : "加载 ChatGPT 建议"}
          </button>
          <button type="button" className="quietButton" onClick={loadReviewedObjects} disabled={suggestionsLoading || !jobId.trim()}>
            {suggestionsLoading ? "加载中..." : "加载已审核结果"}
          </button>
        </div>
        {uploadError && <StateMessage title="上传错误" body={uploadError} />}
        {uploadResult && !uploadError && (
          <div className="uploadResultSummary">
            <span>✅ 已上传</span>
            <span>对象数：{uploadResult.object_count}</span>
            {(uploadResult.warnings || []).length > 0 && <span className="warningPill">⚠ {uploadResult.warnings.length} warnings</span>}
          </div>
        )}
      </div>

      {suggestionsError && <StateMessage title="加载失败" body={suggestionsError} />}

      {hasReviewItems && (
        <div className="importReviewMeta">
          <span>{reviewSource === "reviewed_objects" ? "来源：已审核结果（user_reviewed）" : "来源：ChatGPT AI 建议（ai_suggested）"}</span>
          <span>对象数：{reviewItems.length}</span>
          {reviewSource === "reviewed_objects" && (
            <span className="warningPill">⚠ 正在查看已审核结果，非 AI 原始建议</span>
          )}
        </div>
      )}

      {hasReviewItems && (
        <div className="reviewObjectList">
          <div className="sectionHeader">
            <h3>审核对象</h3>
            <span>{reviewItems.length} 个候选</span>
          </div>
          {reviewItems.map((item, index) => (
            <ReviewObjectCard
              key={item.object_key || index}
              item={item}
              index={index}
              sourceTraceSections={sourceTraceSections}
              onToggleStatus={toggleReviewStatus}
              onEditTag={editTag}
              onRemoveTag={removeTag}
              onAddTag={addTag}
              onSetComment={setUserComment}
              onEditEvidenceField={editEvidenceField}
              onRemoveEvidenceRef={removeEvidenceRef}
              onAddEvidenceRef={addEvidenceRef}
              onSelectSection={selectSectionForRef}
            />
          ))}
        </div>
      )}

      {/* ---- Save Review ---- */}
      {hasReviewItems && (
        <div className="reviewSaveBar">
          <button type="button" className="primaryButton" onClick={saveReview} disabled={saveStatus === "saving" || pipelineRunning}>
            {saveStatus === "saving" ? "保存中..." : "保存审核结果"}
          </button>
          <span className="commitNote">保存审核结果只写入 staging，不写核心数据库。</span>
          {(saveStatus === "saved" || saveStatus === "committed") && saveResult && (
            <div className="saveResultSummary">
              {saveStatus === "saved" && <span>✅ 已保存到 staging</span>}
              {saveStatus === "committed" && <span>✅ 已写入资料库</span>}
              {saveResult.document_id !== undefined && <span>文档 ID：{saveResult.document_id}</span>}
              {(saveResult.status) && <span>状态：{saveResult.status === "already_committed" ? "已提交（无重复写入）" : saveResult.status}</span>}
              {saveResult.message && <span>{saveResult.message}</span>}
              {saveResult.accepted_count !== undefined && <span>接受：{saveResult.accepted_count}</span>}
              {saveResult.edited_count !== undefined && <span>编辑：{saveResult.edited_count}</span>}
              {saveResult.rejected_count !== undefined && <span>拒绝：{saveResult.rejected_count}</span>}
              {(saveResult.inserted_count ?? saveResult.inserted) !== undefined && <span style={{color: "#2e7d32"}}>新增：{saveResult.inserted_count ?? saveResult.inserted}</span>}
              {(saveResult.updated_count ?? saveResult.updated) !== undefined && <span style={{color: "#1565c0"}}>更新：{saveResult.updated_count ?? saveResult.updated}</span>}
              {(saveResult.deprecated_count ?? saveResult.deprecated) !== undefined && <span style={{color: "#e65100"}}>弃用：{saveResult.deprecated_count ?? saveResult.deprecated}</span>}
              {(saveResult.total_active ?? saveResult.total_active_count) !== undefined && <span>活跃对象：{saveResult.total_active ?? saveResult.total_active_count}</span>}
              {saveResult.mapping_status_counts && (
                <span>映射：mapped {saveResult.mapping_status_counts.mapped ?? 0} · partial {saveResult.mapping_status_counts.partial ?? 0} · failed {saveResult.mapping_status_counts.failed ?? 0} · not_mapped {saveResult.mapping_status_counts.not_mapped ?? 0}</span>
              )}
              {saveResult.mapping_status_counts && (saveResult.mapping_status_counts.partial ?? 0) > 0 && (
                <span className="warningPill">⚠ 部分对象证据为 fallback / partial 映射，需要人工复核。</span>
              )}
              {saveResult.mapping_status_counts && (saveResult.mapping_status_counts.failed ?? 0) > 0 && (
                <span className="errorPill" style={{color: "#c62828", fontWeight: 600}}>✗ {saveResult.mapping_status_counts.failed} 条对象证据映射失败，需要修复 evidence_refs。</span>
              )}
              {saveResult.status === "already_committed"
                ? <span className="safetyNote">db_write: {String(saveResult.core_db_write_performed ?? "—")}（already_committed 无需写入）</span>
                : <span className="safetyNote">db_write: {String(saveResult.core_db_write_performed ?? saveResult.db_write_performed ?? "—")}</span>
              }
            </div>
          )}
          {saveStatus === "error" && saveResult?.error && (
            <StateMessage title="保存/提交失败" body={saveResult.error} />
          )}
        </div>
      )}

      {/* ---- Three-Phase Pipeline Commit ---- */}
      {hasReviewItems && (
        <div className="commitPipeline">
          <div className="sectionHeader">
            <h3>提交入库流程</h3>
            <span>{pipelinePhaseSummary(commitPhase)}</span>
          </div>

          <div className="commitPipelineStages">
            <div className={commitPhaseRowClass(commitPhase.paper)}>
              {phaseIcon(commitPhase.paper)}
              <span className="commitPhaseLabel">1. 论文正文入库</span>
              <span className="commitPhaseStatus">{phaseLabel(commitPhase.paper)}</span>
              {commitPhase.paper?.status === "ok" && commitPhase.paper?.result?.document_id && (
                <code className="commitPhaseDetail">doc_id={commitPhase.paper.result.document_id}</code>
              )}
            </div>

            <div className={commitPhaseRowClass(commitPhase.remap)}>
              {phaseIcon(commitPhase.remap)}
              <span className="commitPhaseLabel">2. 证据映射预览</span>
              <span className="commitPhaseStatus">{phaseLabel(commitPhase.remap)}</span>
              {(commitPhase.remap?.status === "ok" || commitPhase.remap?.status === "warning") && commitPhase.remap?.result?.summary && (
                <code className="commitPhaseDetail">
                  mapped {commitPhase.remap.result.summary.mapped ?? 0} · partial {commitPhase.remap.result.summary.partial ?? 0} · failed {commitPhase.remap.result.summary.failed ?? 0}
                </code>
              )}
              {(commitPhase.remap?.status === "ok" || commitPhase.remap?.status === "warning") && (commitPhase.remap?.result?.summary?.partial ?? 0) > 0 && (
                <span className="warningPill" style={{marginLeft: 8}}>⚠ partial 映射需人工复核</span>
              )}
            </div>

            <div className={commitPhaseRowClass(commitPhase.objects)}>
              {phaseIcon(commitPhase.objects)}
              <span className="commitPhaseLabel">3. 对象候选入库</span>
              <span className="commitPhaseStatus">{phaseLabel(commitPhase.objects)}</span>
              {(commitPhase.objects?.status === "ok" || commitPhase.objects?.status === "already_committed") && saveResult && (
                <code className="commitPhaseDetail">
                  {(saveResult.inserted_count ?? saveResult.inserted) !== undefined && `new ${saveResult.inserted_count ?? saveResult.inserted} `}
                  {(saveResult.updated_count ?? saveResult.updated) !== undefined && `upd ${saveResult.updated_count ?? saveResult.updated} `}
                  {(saveResult.deprecated_count ?? saveResult.deprecated) !== undefined && `dep ${saveResult.deprecated_count ?? saveResult.deprecated}`}
                </code>
              )}
            </div>
          </div>

          {/* Confirmation dialog when remap has failures */}
          {confirmRemapFailed && commitPhase.remap?.status === "warning" && (
            <div className="remapWarningConfirm">
              <p>
                ⚠ 证据映射预览发现 <strong>{commitPhase.remap.result?.summary?.failed || 0}</strong> 条映射失败。
                部分对象可能无法正确关联到已入库的证据片段。
              </p>
              <div className="remapWarningActions">
                <button type="button" className="primaryButton" onClick={continueFromRemap}>
                  仍然提交对象
                </button>
                <button type="button" className="quietButton" onClick={() => setState(s => ({ ...s, confirmRemapFailed: false }))}>
                  取消
                </button>
              </div>
            </div>
          )}

          {/* Remap detailed preview */}
          {remapPreview && !remapPreview.error && commitPhase.remap && commitPhase.remap.status !== "pending" && commitPhase.remap.status !== "running" && (
            <div className="remapPreviewSection">
              <div className="sectionHeader">
                <span>document_id={remapPreview.document_id} · {remapPreview.object_count} 对象 · chunks={remapPreview.chunk_index_size}</span>
              </div>
              {remapPreview.summary && (
                <div className="remapSummaryBar">
                  <span style={{color: "#2e7d32"}}>mapped: {remapPreview.summary.mapped}</span>
                  <span style={{color: "#1565c0"}}>partial: {remapPreview.summary.partial}</span>
                  <span style={{color: "#c62828"}}>failed: {remapPreview.summary.failed}</span>
                  <span style={{color: "#6d6d6d"}}>not_mapped: {remapPreview.summary.not_mapped}</span>
                  <span className="safetyNote">core_db_write_performed: {String(remapPreview.core_db_write_performed)}</span>
                </div>
              )}
              <div className="reviewObjectList">
                {(remapPreview.objects || []).map(obj => (
                  <article key={obj.object_key} className="reviewObjectCard">
                    <div className="reviewCardHeader">
                      <div className="cardMeta">
                        <span>{obj.object_name}</span>
                        <span>{obj.review_status}</span>
                        <span style={{
                          fontWeight: 600,
                          color: obj.mapping_status === "mapped" ? "#2e7d32" :
                                 obj.mapping_status === "partial" ? "#1565c0" :
                                 obj.mapping_status === "failed" ? "#c62828" :
                                 obj.mapping_status === "skipped" ? "#9e9e9e" : "#6d6d6d"
                        }}>{obj.mapping_status}</span>
                      </div>
                      {obj.mapped_chunk_ids.length > 0 && (
                        <code className="objectKeyLabel">chunks: [{obj.mapped_chunk_ids.join(", ")}]</code>
                      )}
                      {(obj.warnings || []).length > 0 && (
                        <div className="reviewWarnings">
                          {obj.warnings.map((w, wi) => <span key={wi}>{typeof w === "string" ? w : w.warning || w.message || JSON.stringify(w)}</span>)}
                        </div>
                      )}
                    </div>
                    {(obj.evidence_ref_results || []).length > 0 && (
                      <details className="remapRefDetails">
                        <summary>证据映射详情（{obj.evidence_ref_results.length} 条）</summary>
                        {obj.evidence_ref_results.map((ref, ri) => (
                          <div key={ri} className="remapRefRow" style={{
                            borderLeft: `3px solid ${
                              ref.match_type === "exact" ? "#2e7d32" :
                              ref.match_type === "normalized" ? "#1565c0" :
                              ref.match_type === "nearby_page" ? "#e65100" :
                              ref.match_type === "fallback" ? "#f9a825" : "#c62828"
                            }`,
                            padding: "4px 8px", margin: "4px 0", fontSize: "0.85rem"
                          }}>
                            <div><strong>{ref.section_title || "(no section)"}</strong> · p.{ref.pdf_page} · {ref.match_type}</div>
                            <div style={{color: "#666", fontStyle: "italic"}}>"{ref.quote_text_short?.substring(0, 80)}{ref.quote_text_short?.length > 80 ? "..." : ""}"</div>
                            {ref.matched_chunk_id && <div>→ chunk #{ref.matched_chunk_id}</div>}
                            {ref.warning && <div style={{color: "#c62828"}}>⚠ {ref.warning}</div>}
                          </div>
                        ))}
                      </details>
                    )}
                  </article>
                ))}
              </div>
            </div>
          )}

          {remapPreview?.error && <StateMessage title="映射预览失败" body={remapPreview.error} />}

          {/* Action buttons */}
          <div className="reviewSaveBar">
            <button type="button" className="primaryButton" onClick={commitFullPipeline} disabled={commitLoading || pipelineRunning}>
              {pipelineRunning ? "提交中..." : commitLoading ? "载入中..." : "载入资料库"}
            </button>
            <button type="button" onClick={loadRemapPreview} disabled={remapLoading || pipelineRunning} className="quietButton">
              {remapLoading ? "映射中..." : "预览证据映射"}
            </button>
            <span className="commitNote">完整流程：论文入库 → 证据映射预览 → 对象候选入库。映射预览失败时需手动确认。</span>
          </div>

          {/* Post-commit navigation */}
          {(commitPhase.objects?.status === "ok" || commitPhase.objects?.status === "already_committed") && (
            <div className="commitNavLinks">
              <button type="button" onClick={() => onNavigate?.("readShelf")}>
                查看已读书架
              </button>
              <button type="button" onClick={() => onNavigate?.("search")}>
                搜索对象
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

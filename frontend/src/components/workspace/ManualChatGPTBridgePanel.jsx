import { useEffect, useRef, useState } from "react";
import { postJson } from "../../api/client.js";
import { apiErrorMessage, sourceModeLabel } from "../../shared/utils/display.js";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import { buildSourceSelectionKey, normalizeSourceIds, normalizeSourceRefs } from "./sourceTargets.js";

const SOURCE_PACK_PREVIEW_PATH = "/api/v1/library/manual-chatgpt-bridge/source-pack-preview";
const PROMPT_EXPORT_PATH = "/api/v1/library/manual-chatgpt-bridge/prompt-export";
const VALIDATE_PASTEBACK_PATH = "/api/v1/library/manual-chatgpt-bridge/validate-pasteback";
const PACKET_PREVIEW_PATH = "/api/v1/library/mechanism-draft-review/packet-preview";

export default function ManualChatGPTBridgePanel({ documentId, chapterId, selectionTarget, onOpenReview }) {
  const [selectionState, setSelectionState] = useState({ status: "idle", result: null, error: "" });
  const [sourcePackText, setSourcePackText] = useState("");
  const [sourcePackResult, setSourcePackResult] = useState(null);
  const [promptState, setPromptState] = useState({ status: "idle", result: null, error: "" });
  const [pastebackText, setPastebackText] = useState("");
  const [validationState, setValidationState] = useState({ status: "idle", result: null, error: "" });
  const [handoffState, setHandoffState] = useState({ status: "idle", error: "" });
  const [copyState, setCopyState] = useState("idle");
  const selectionKey = buildSourceSelectionKey(selectionTarget);
  const activeSelectionKey = useRef(selectionKey);

  useEffect(() => {
    activeSelectionKey.current = selectionKey;
    setSelectionState({ status: "idle", result: null, error: "" });
    setSourcePackText("");
    setSourcePackResult(null);
    setPromptState({ status: "idle", result: null, error: "" });
    setPastebackText("");
    setValidationState({ status: "idle", result: null, error: "" });
    setHandoffState({ status: "idle", error: "" });
    setCopyState("idle");
  }, [selectionKey]);

  function selectionIsCurrent(requestSelectionKey) {
    return activeSelectionKey.current === requestSelectionKey;
  }

  const promptPackage = promptState.result;
  const validation = validationState.result;

  function handleSourcePackChange(value) {
    setSelectionState({ status: "idle", result: null, error: "" });
    setSourcePackText(value);
    setSourcePackResult(null);
    setPromptState({ status: "idle", result: null, error: "" });
    setPastebackText("");
    setValidationState({ status: "idle", result: null, error: "" });
    setHandoffState({ status: "idle", error: "" });
    setCopyState("idle");
  }

  function handlePastebackChange(value) {
    setPastebackText(value);
    setValidationState({ status: "idle", result: null, error: "" });
    setHandoffState({ status: "idle", error: "" });
  }

  async function handleBuildSelectionSourcePack() {
    if (!selectionTarget) return;
    const requestSelectionKey = selectionKey;
    setSelectionState({ status: "loading", result: null, error: "" });
    try {
      const objectCandidateIds = normalizeSourceIds([
        ...normalizeSourceIds(selectionTarget.objectCandidateIds),
        selectionTarget.objectCandidateId,
      ]);
      const reviewedObjectRefs = normalizeSourceRefs([
        ...normalizeSourceRefs(selectionTarget.reviewedObjectRefs),
        selectionTarget.candidateTempId,
      ]);
      const result = await postJson(SOURCE_PACK_PREVIEW_PATH, {
        document_id: documentId,
        chapter_id: chapterId,
        chunk_id: selectionTarget.matchedChunkId || null,
        server_note_id: selectionTarget.serverNoteId || null,
        client_note_id: selectionTarget.clientNoteId || null,
        object_candidate_ids: objectCandidateIds,
        reviewed_object_refs: reviewedObjectRefs,
      });
      if (!selectionIsCurrent(requestSelectionKey)) return;
      if (!result.selection_ready) {
        setSelectionState({ status: "blocked", result, error: blockerMessage(result.blockers) });
        return;
      }
      const sourcePack = result.source_pack_result;
      setSelectionState({ status: "ready", result, error: "" });
      setSourcePackText(JSON.stringify(sourcePack, null, 2));
      setPastebackText("");
      setValidationState({ status: "idle", result: null, error: "" });
      setHandoffState({ status: "idle", error: "" });
      await exportPrompt(sourcePack, requestSelectionKey);
    } catch (error) {
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setSelectionState({ status: "error", result: null, error: apiErrorMessage(error) });
    }
  }

  async function handleExportPrompt() {
    let sourcePack;
    try {
      const parsed = parseJsonObject(sourcePackText);
      sourcePack = parsed.source_pack_result || parsed;
      if (!sourcePack.mechanism_source_pack) {
        throw new Error("mechanism_source_pack_missing");
      }
    } catch (error) {
      setPromptState({ status: "error", result: null, error: sourcePackErrorMessage(error) });
      return;
    }
    await exportPrompt(sourcePack, selectionKey);
  }

  async function exportPrompt(sourcePack, requestSelectionKey = selectionKey) {
    setPromptState({ status: "loading", result: null, error: "" });
    setValidationState({ status: "idle", result: null, error: "" });
    try {
      const result = await postJson(PROMPT_EXPORT_PATH, {
        source_pack_result: sourcePack,
        chapter_id: chapterId || null,
        import_batch_id: documentId && chapterId ? `workspace-${documentId}-${chapterId}` : null,
      });
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setSourcePackResult(sourcePack);
      setPromptState({
        status: result.status === "OK" ? "ready" : "blocked",
        result,
        error: result.status === "OK" ? "" : blockerMessage(result.blockers),
      });
    } catch (error) {
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setPromptState({ status: "error", result: null, error: apiErrorMessage(error) });
    }
  }
  async function handleCopyPrompt() {
    if (!promptPackage?.copy_ready_prompt) return;
    setCopyState("copying");
    try {
      await copyTextToClipboard(promptPackage.copy_ready_prompt);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  async function handleValidatePasteback() {
    if (!sourcePackResult) return;
    const requestSelectionKey = selectionKey;
    let pastedJson;
    try {
      pastedJson = parseJsonObject(pastebackText);
    } catch {
      setValidationState({ status: "error", result: null, error: "粘回内容必须是一个 JSON object。" });
      return;
    }
    setValidationState({ status: "loading", result: null, error: "" });
    setHandoffState({ status: "idle", error: "" });
    try {
      const result = await postJson(VALIDATE_PASTEBACK_PATH, {
        source_pack_result: sourcePackResult,
        pasted_chatgpt_response_json: pastedJson,
      });
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setValidationState({
        status: result.validator_passed ? "ready" : "invalid",
        result,
        error: result.validator_passed ? "" : validationErrorMessage(result),
      });
    } catch (error) {
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setValidationState({ status: "error", result: null, error: apiErrorMessage(error) });
    }
  }

  async function handleOpenReview() {
    if (!sourcePackResult || !validation?.validator_passed) return;
    const requestSelectionKey = selectionKey;
    setHandoffState({ status: "loading", error: "" });
    const inputBundle = {
      pasteback_validation_result: validation,
      source_pack_result: sourcePackResult,
    };
    try {
      const packetResult = await postJson(PACKET_PREVIEW_PATH, inputBundle);
      if (!selectionIsCurrent(requestSelectionKey)) return;
      if (!packetResult.review_ready) {
        setHandoffState({ status: "blocked", error: blockerMessage(packetResult.blockers) });
        return;
      }
      setHandoffState({ status: "ready", error: "" });
      onOpenReview?.({ inputBundle, packetResult });
    } catch (error) {
      if (!selectionIsCurrent(requestSelectionKey)) return;
      setHandoffState({ status: "error", error: apiErrorMessage(error) });
    }
  }

  return (
    <section className="workspaceStudioFeatureCard manualChatGPTBridgePanel" aria-label="Manual ChatGPT Bridge">
      <div className="workspaceStudioFeatureHeader">
        <div>
          <p className="workspaceKicker">P6 · Manual bridge</p>
          <h4>Prompt 导出与手动粘回</h4>
        </div>
        <WorkspaceStatusPill status="read_only">无自动调用</WorkspaceStatusPill>
      </div>

      <section className="manualBridgeSelection" aria-label="当前选择 source pack readiness">
        <div>
          <span>当前选择</span>
          <strong>{selectionLabel(selectionTarget)}</strong>
          <small>{selectionScopeLabel(selectionTarget, documentId, chapterId)}</small>
        </div>
        <button
          type="button"
          className="manualBridgeSecondaryAction"
          disabled={!selectionTarget || selectionState.status === "loading"}
          onClick={handleBuildSelectionSourcePack}
        >
          {selectionState.status === "loading" ? "绑定中..." : "使用当前选择构建 Prompt"}
        </button>
      </section>
      {selectionState.error ? <BridgeNotice tone="error">{selectionState.error}</BridgeNotice> : null}
      {selectionState.status === "ready" ? (
        <BridgeNotice tone="success">当前 note 与 chunk 已通过同章节精确绑定。</BridgeNotice>
      ) : null}

      <label className="mechanismDraftReviewInput">
        <span>Mechanism source pack</span>
        <textarea
          value={sourcePackText}
          onChange={(event) => handleSourcePackChange(event.target.value)}
          placeholder='{"mechanism_source_pack": {...}}'
          spellCheck="false"
        />
      </label>
      <button
        type="button"
        className="mechanismDraftPrimaryAction"
        disabled={!sourcePackText.trim() || promptState.status === "loading"}
        onClick={handleExportPrompt}
      >
        {promptState.status === "loading" ? "构建中..." : "构建手动 Prompt"}
      </button>
      {promptState.error ? <BridgeNotice tone="error">{promptState.error}</BridgeNotice> : null}

      {promptPackage?.status === "OK" ? (
        <div className="manualBridgeFlow">
          <section className="manualBridgeStatusLine" aria-label="Prompt export status">
            <WorkspaceStatusPill status="available">Prompt 已就绪</WorkspaceStatusPill>
            <span>{sourceModeLabel(promptPackage.prompt_payload_json?.source_mode)}</span>
            <span>{promptPackage.binding_mode}</span>
          </section>

          <label className="mechanismDraftReviewInput compact manualBridgePrompt">
            <span>复制给 ChatGPT 的 Prompt</span>
            <textarea readOnly value={promptPackage.copy_ready_prompt || ""} spellCheck="false" />
          </label>
          <button type="button" className="manualBridgeSecondaryAction" onClick={handleCopyPrompt}>
            {copyState === "copied" ? "已复制 Prompt" : copyState === "copying" ? "复制中..." : "复制 Prompt"}
          </button>
          {copyState === "error" ? <BridgeNotice tone="error">浏览器未允许复制，请手动选择 Prompt 文本。</BridgeNotice> : null}

          <details className="workspaceDisclosure manualBridgeSchemaDisclosure">
            <summary>预期 JSON schema</summary>
            <pre>{JSON.stringify(promptPackage.expected_response_schema || {}, null, 2)}</pre>
          </details>

          <label className="mechanismDraftReviewInput">
            <span>ChatGPT 粘回 JSON</span>
            <textarea
              value={pastebackText}
              onChange={(event) => handlePastebackChange(event.target.value)}
              placeholder='{"source_mode": "joint_led", ...}'
              spellCheck="false"
            />
          </label>
          <button
            type="button"
            className="mechanismDraftPrimaryAction"
            disabled={!pastebackText.trim() || validationState.status === "loading"}
            onClick={handleValidatePasteback}
          >
            {validationState.status === "loading" ? "校验中..." : "校验粘回 JSON"}
          </button>
        </div>
      ) : null}

      {validationState.error ? <BridgeNotice tone="error">{validationState.error}</BridgeNotice> : null}
      {validation?.validator_passed ? (
        <section className="manualBridgeValidationReady" aria-live="polite">
          <div>
            <WorkspaceStatusPill status="reviewed">Validator passed</WorkspaceStatusPill>
            <strong>草稿候选已进入只读 pending preview</strong>
          </div>
          <span>
            warnings {validation.validation_report?.warnings?.length || 0} · errors {validation.validation_report?.errors?.length || 0}
          </span>
          <button
            type="button"
            className="mechanismDraftPrimaryAction"
            disabled={handoffState.status === "loading"}
            onClick={handleOpenReview}
          >
            {handoffState.status === "loading" ? "准备审核包..." : "进入机制草稿审核"}
          </button>
        </section>
      ) : null}
      {handoffState.error ? <BridgeNotice tone="error">{handoffState.error}</BridgeNotice> : null}

      <div className="mechanismDraftSafetyBoundary">
        手动复制/粘回 · 不调用 LLM API · 不写数据库 · 不生成正式 mechanism card
      </div>
    </section>
  );
}

function BridgeNotice({ tone, children }) {
  return <p className={`mechanismDraftReviewNotice ${tone}`}>{children}</p>;
}

function parseJsonObject(value) {
  const parsed = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("json_object_required");
  }
  return parsed;
}

function sourcePackErrorMessage(error) {
  if (error?.message === "mechanism_source_pack_missing") {
    return "输入必须包含 mechanism_source_pack。";
  }
  return "Source pack 必须是一个 JSON object。";
}

function validationErrorMessage(result) {
  const report = result?.validation_report || {};
  const issues = [...(report.errors || []), ...(report.warnings || [])];
  return issues.length ? `Validator 未通过：${issues.join(" · ")}` : "Validator 未通过。";
}

function blockerMessage(blockers) {
  const values = Array.isArray(blockers) ? blockers : [];
  return values.length ? `流程被阻止：${values.join(" · ")}` : "只读 gate 未通过。";
}

function selectionLabel(target) {
  if (!target) return "尚未选择 note/source";
  if (target.noteText) return target.noteText;
  if (target.selectedText) return target.selectedText;
  return target.serverNoteId || target.clientNoteId || "当前来源";
}

function selectionScopeLabel(target, documentId, chapterId) {
  if (!target) return `document ${documentId || "-"} · chapter ${chapterId || "-"}`;
  const noteId = target.serverNoteId || target.clientNoteId || "note missing";
  const chunk = target.matchedChunkId ? `chunk ${target.matchedChunkId}` : "chunk missing";
  return `${noteId} · ${chunk}`;
}
async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

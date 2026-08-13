import { useState } from "react";
import { postJson } from "../../api/client.js";
import { apiErrorMessage, sourceModeLabel } from "../../shared/utils/display.js";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

const PACKET_PREVIEW_PATH = "/api/v1/library/mechanism-draft-review/packet-preview";
const ACTION_PREVIEW_PATH = "/api/v1/library/mechanism-draft-review/action-preview";

const REVIEW_ACTIONS = [
  { action: "accept", label: "接受草稿" },
  { action: "needs_edit", label: "需修改" },
  { action: "reject", label: "拒绝" },
  { action: "defer", label: "稍后审核" },
  { action: "merge_into", label: "合并预览" },
];

export default function MechanismDraftReviewPanel({
  documentId,
  chapterId,
  initialBundle = null,
  initialPacketResult = null,
}) {
  const [inputText, setInputText] = useState(() => serializeInputBundle(initialBundle));
  const [packetState, setPacketState] = useState(() => (
    initialPacketResult?.review_ready
      ? { status: "ready", result: initialPacketResult, error: "" }
      : { status: "idle", result: null, error: "" }
  ));
  const [reviewNotes, setReviewNotes] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [actionState, setActionState] = useState({ status: "idle", result: null, error: "" });

  const packet = packetState.result?.review_packet || null;

  async function handleBuildPacket() {
    let parsed;
    try {
      parsed = JSON.parse(inputText);
    } catch {
      setPacketState({
        status: "error",
        result: null,
        error: "JSON 格式无效，请粘贴 P6 validator 输出与 source pack。",
      });
      return;
    }
    const pastebackValidationResult = parsed?.pasteback_validation_result;
    const sourcePackResult = parsed?.source_pack_result;
    if (!pastebackValidationResult || !sourcePackResult) {
      setPacketState({
        status: "error",
        result: null,
        error: "需要 pasteback_validation_result 和 source_pack_result 两个字段。",
      });
      return;
    }
    setPacketState({ status: "loading", result: null, error: "" });
    setActionState({ status: "idle", result: null, error: "" });
    try {
      const result = await postJson(PACKET_PREVIEW_PATH, {
        pasteback_validation_result: pastebackValidationResult,
        source_pack_result: sourcePackResult,
      });
      setPacketState({
        status: result.review_ready ? "ready" : "blocked",
        result,
        error: result.review_ready ? "" : blockerMessage(result.blockers),
      });
    } catch (error) {
      setPacketState({ status: "error", result: null, error: apiErrorMessage(error) });
    }
  }

  async function handleActionPreview(action) {
    if (!packet) return;
    setActionState({ status: "loading", result: null, error: "" });
    try {
      const result = await postJson(ACTION_PREVIEW_PATH, {
        review_packet: packet,
        action,
        review_notes: reviewNotes || null,
        merge_into_packet_id: action === "merge_into" ? mergeTarget || null : null,
      });
      setActionState({
        status: result.status === "OK" ? "ready" : "blocked",
        result,
        error: result.status === "OK" ? "" : blockerMessage(result.blockers),
      });
    } catch (error) {
      setActionState({ status: "error", result: null, error: apiErrorMessage(error) });
    }
  }

  return (
    <section className="workspaceStudioFeatureCard mechanismDraftReviewPanel" aria-label="机制草稿只读审核">
      <div className="workspaceStudioFeatureHeader">
        <div>
          <p className="workspaceKicker">P7 · Draft review</p>
          <h4>机制草稿审核</h4>
        </div>
        <WorkspaceStatusPill status="read_only">只读预览</WorkspaceStatusPill>
      </div>

      <p className="mechanismDraftReviewContext">
        当前范围：document {documentId || "-"} · chapter {chapterId || "-"}。输入必须来自 P6 validator；不会自动调用模型。
      </p>

      <label className="mechanismDraftReviewInput">
        <span>P6 校验结果与双源包</span>
        <textarea
          value={inputText}
          onChange={(event) => setInputText(event.target.value)}
          placeholder='{"pasteback_validation_result": {...}, "source_pack_result": {...}}'
          spellCheck="false"
        />
      </label>
      <button
        type="button"
        className="mechanismDraftPrimaryAction"
        disabled={!inputText.trim() || packetState.status === "loading"}
        onClick={handleBuildPacket}
      >
        {packetState.status === "loading" ? "校验中..." : "校验并打开审核"}
      </button>

      {packetState.error ? <ReviewNotice tone="error">{packetState.error}</ReviewNotice> : null}
      {packet ? (
        <MechanismReviewWorkspace
          packet={packet}
          reviewNotes={reviewNotes}
          mergeTarget={mergeTarget}
          actionState={actionState}
          onReviewNotesChange={setReviewNotes}
          onMergeTargetChange={setMergeTarget}
          onActionPreview={handleActionPreview}
        />
      ) : (
        <ReviewEmptyState />
      )}
    </section>
  );
}

function MechanismReviewWorkspace({
  packet,
  reviewNotes,
  mergeTarget,
  actionState,
  onReviewNotesChange,
  onMergeTargetChange,
  onActionPreview,
}) {
  const draft = packet.draft_summary || {};
  const parity = packet.source_parity || {};
  const material = packet.source_material || {};
  const validation = packet.validation || {};
  const linkedObjects = packet.linked_objects || {};
  const note = material.primary_user_note || {};
  const excerpt = material.primary_source_excerpt || {};

  return (
    <div className="mechanismDraftReviewWorkspace" data-review-packet-id={packet.packet_id}>
      <section className="mechanismDraftSummary">
        <div>
          <span className="mechanismDraftModeChip">{sourceModeLabel(parity.source_mode)}</span>
          <h5>{draft.mechanism_name_cn || draft.mechanism_name_en || draft.mechanism_key || "未命名机制草稿"}</h5>
        </div>
        <WorkspaceStatusPill status="planned">待人工审核</WorkspaceStatusPill>
        <p>{draft.short_explanation || "暂无短说明。"}</p>
      </section>

      <section className="mechanismPrimarySources" aria-label="平等 primary sources">
        <PrimarySourceCard
          label="Primary source · 用户笔记"
          body={note.note_text || note.user_note_text}
          contribution={parity.user_note_contribution}
          citation={firstMatchingCitation(material.citation_tokens, "note:")}
        />
        <PrimarySourceCard
          label="Primary source · 原文片段"
          body={excerpt.selected_text || excerpt.chunk_text}
          contribution={parity.source_excerpt_contribution}
          citation={firstMatchingCitation(material.citation_tokens, "chunk:")}
        />
      </section>

      <details className="workspaceDisclosure mechanismDraftReviewDetails">
        <summary>证据对齐、对象与 validator</summary>
        <ReviewFact label="证据对齐" value={parity.evidence_alignment} />
        <ReviewFact label="对象贡献" value={parity.linked_object_contribution} />
        <ReviewFact
          label="对象角色"
          value={`${linkedObjects.role || "semantic_support_not_mechanism"} · ${linkedObjects.approved_objects?.length || 0} 个已审核对象`}
        />
        <ReviewIssueList title="Source balance warnings" items={parity.source_balance_warnings} />
        <ReviewIssueList title="Validator warnings" items={validation.warnings} />
        <ReviewIssueList title="Validator errors" items={validation.errors} />
      </details>

      <label className="mechanismDraftReviewInput compact">
        <span>审核备注</span>
        <textarea
          value={reviewNotes}
          onChange={(event) => onReviewNotesChange(event.target.value)}
          placeholder="记录判断依据；当前仅生成动作预览。"
        />
      </label>
      <label className="mechanismDraftReviewInput compact">
        <span>合并目标 packet ID</span>
        <input
          value={mergeTarget}
          onChange={(event) => onMergeTargetChange(event.target.value)}
          placeholder="仅合并预览时需要"
        />
      </label>

      <div className="mechanismDraftReviewActions" aria-label="机制草稿审核动作预览">
        {REVIEW_ACTIONS.map((item) => (
          <button
            type="button"
            key={item.action}
            disabled={actionState.status === "loading"}
            onClick={() => onActionPreview(item.action)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {actionState.error ? <ReviewNotice tone="error">{actionState.error}</ReviewNotice> : null}
      {actionState.result ? <ActionPreview result={actionState.result} /> : null}
      <div className="mechanismDraftSafetyBoundary">
        不写数据库 · 不生成正式 mechanism card · 不保存 relation · 不写 Zotero/vector
      </div>
    </div>
  );
}

function PrimarySourceCard({ label, body, contribution, citation }) {
  return (
    <article className="mechanismPrimarySourceCard">
      <span>{label}</span>
      <p>{body || "该 primary source 缺少可显示文本。"}</p>
      <small>{contribution || "未提供贡献说明。"}</small>
      {citation ? <code>{citation}</code> : null}
    </article>
  );
}

function ReviewFact({ label, value }) {
  return (
    <div className="mechanismDraftReviewFact">
      <strong>{label}</strong>
      <p>{value || "未提供"}</p>
    </div>
  );
}

function ReviewIssueList({ title, items }) {
  const values = Array.isArray(items) ? items : [];
  return (
    <div className="mechanismDraftReviewFact">
      <strong>{title}</strong>
      <p>{values.length ? values.join(" · ") : "无"}</p>
    </div>
  );
}

function ActionPreview({ result }) {
  return (
    <ReviewNotice tone={result.status === "OK" ? "success" : "error"}>
      动作预览：{actionLabel(result.requested_action)} → {result.proposed_review_status || "未变更"}；
      正式机制卡：{result.creates_formal_mechanism_card ? "会创建" : "不会创建"}。
    </ReviewNotice>
  );
}

function ReviewNotice({ tone, children }) {
  return <p className={`mechanismDraftReviewNotice ${tone}`}>{children}</p>;
}

function ReviewEmptyState() {
  return (
    <div className="mechanismDraftReviewEmpty">
      <strong>等待经过 validator 的手动粘回结果</strong>
      <p>审核入口不会读取旧版候选冒充双源草稿，也不会自动调用 ChatGPT。</p>
    </div>
  );
}

function firstMatchingCitation(values, prefix) {
  return (Array.isArray(values) ? values : []).find((value) => String(value).startsWith(prefix)) || "";
}

function actionLabel(action) {
  return REVIEW_ACTIONS.find((item) => item.action === action)?.label || action || "未知动作";
}

function blockerMessage(blockers) {
  const values = Array.isArray(blockers) ? blockers : [];
  return values.length ? `审核被阻止：${values.join(" · ")}` : "审核包未通过只读 gate。";
}

function serializeInputBundle(value) {
  return value ? JSON.stringify(value, null, 2) : "";
}

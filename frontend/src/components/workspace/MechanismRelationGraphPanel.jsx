import { useEffect, useMemo, useState } from "react";
import ManualChatGPTBridgePanel from "./ManualChatGPTBridgePanel.jsx";
import MechanismDraftReviewPanel from "./MechanismDraftReviewPanel.jsx";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import { buildSourceSelectionKey } from "./sourceTargets.js";

const NODE_POSITIONS = {
  evidence_overview: { x: 18, y: 52 },
  note_overview: { x: 34, y: 27 },
  approved_objects: { x: 52, y: 48 },
  relation_dry_run: { x: 73, y: 34 },
  mechanism_readiness: { x: 78, y: 69 },
  pn68_quarantine: { x: 31, y: 75 },
};

const FALLBACK_GRAPH = {
  status: "available_read_only",
  node_counts: {},
  nodes: [],
  edges: [],
  pn68: {
    quarantined: false,
    excluded_from_relation_dry_run: false,
    positive_relation_source: false,
  },
  positive_relation_sources: {
    approved_object_candidates_only: true,
    rejected_candidates_included: false,
    pending_candidates_included: false,
    pn68_included: false,
  },
  phase7h_entered: false,
  relation_saved: false,
  mechanism_generated: false,
};

export default function MechanismRelationGraphPanel({
  state = {},
  focusTarget = null,
  selectionTarget = null,
  documentId = null,
  chapterId = null,
}) {
  const graph = state.graph_preview || state.workspace_contract?.right_panel?.graph_preview || FALLBACK_GRAPH;
  const objectSummary = state.object_candidate_dry_run_summary || {};
  const [activeNodeId, setActiveNodeId] = useState("approved_objects");
  const [activeTool, setActiveTool] = useState("graph");
  const [reviewHandoff, setReviewHandoff] = useState(null);
  const [reviewHandoffRevision, setReviewHandoffRevision] = useState(0);
  const focusNodeId = graphFocusNodeId(focusTarget);
  const selectionKey = buildSourceSelectionKey(selectionTarget);

  useEffect(() => {
    if (focusNodeId) setActiveNodeId(focusNodeId);
  }, [focusNodeId]);
  useEffect(() => {
    setReviewHandoff((current) => (
      current?.selectionKey === selectionKey ? current : null
    ));
  }, [selectionKey]);


  const nodes = useMemo(() => buildDisplayNodes(graph), [graph]);
  const edges = useMemo(() => buildDisplayEdges(graph, nodes), [graph, nodes]);
  const activeNode = nodes.find((node) => node.id === activeNodeId) || nodes[0] || null;
  const counts = graph.node_counts || {};
  const correction = state.correction_review_status || {};
  const classification = state.classification_review_status || {};

  function handleOpenReview(handoff) {
    setReviewHandoff({ ...handoff, selectionKey });
    setReviewHandoffRevision((revision) => revision + 1);
    setActiveTool("review");
  }

  return (
    <div className="workspacePanelStack mechanismGraphPanel" aria-label="Studio 与机制关系图谱预览">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">Studio</p>
          <h3>Studio</h3>
        </div>
        <WorkspaceStatusPill status="read_only">图谱只读</WorkspaceStatusPill>
      </div>

      <section className="workspaceStudioToolGrid" aria-label="Search Studio tools">
        <button
          type="button"
          className={studioToolCardClass(activeTool === "graph")}
          aria-pressed={activeTool === "graph"}
          onClick={() => setActiveTool("graph")}
        >
          <span>机制与关系图谱</span>
          <small>只读关系候选与机制 readiness</small>
        </button>
        <article className="workspaceStudioToolCard">
          <span>对象</span>
          <small>已审核对象候选</small>
        </article>
        <article className="workspaceStudioToolCard">
          <span>笔记</span>
          <small>Zotero inspiration notes</small>
        </article>
        <article className="workspaceStudioToolCard">
          <span>PDF 证据</span>
          <small>定位当前证据来源</small>
        </article>
        <button
          type="button"
          className={studioToolCardClass(activeTool === "bridge")}
          aria-pressed={activeTool === "bridge"}
          onClick={() => setActiveTool("bridge")}
        >
          <span>生成 prompt / 手动桥接</span>
          <small>P5 source pack · P6 validator</small>
        </button>
        <button
          type="button"
          className={studioToolCardClass(activeTool === "review")}
          aria-pressed={activeTool === "review"}
          onClick={() => setActiveTool("review")}
        >
          <span>机制草稿审核</span>
          <small>{reviewHandoff ? "已校验审核包就绪" : "P7 只读动作预览"}</small>
        </button>
      </section>

      <section
        className="workspaceStudioFeatureCard graphStudioCard"
        aria-label="机制与关系图谱"
        hidden={activeTool !== "graph"}
      >
        <div className="workspaceStudioFeatureHeader">
          <div>
            <p className="workspaceKicker">图谱</p>
            <h4>机制与关系图谱</h4>
          </div>
          <WorkspaceStatusPill status="read_only">只读</WorkspaceStatusPill>
        </div>

        <section className="mechanismGraphChipRow" aria-label="graph preview status chips">
          <WorkspaceStatusPill status="reviewed">已审核对象 {Number(counts.approved_object_candidates || objectSummary.approved_candidate_count || 0)}</WorkspaceStatusPill>
          <WorkspaceStatusPill status="available">关系候选 {Number(counts.relation_dry_run_candidates || objectSummary.relation_candidate_count || 0)}</WorkspaceStatusPill>
          <WorkspaceStatusPill status="warning">PN68 已排除</WorkspaceStatusPill>
        </section>

        <section className="mechanismGraphViewport" aria-label="只读图谱视图">
          <svg className="mechanismGraphEdges" viewBox="0 0 100 100" role="img" aria-label="关系 dry-run 图谱边">
            {edges.map((edge) => (
              <line
                key={edge.id}
                x1={edge.from.x}
                y1={edge.from.y}
                x2={edge.to.x}
                y2={edge.to.y}
                className={edgeHighlights(edge, activeNodeId) ? "focused" : ""}
              />
            ))}
          </svg>
          {nodes.map((node) => (
            <button
              type="button"
              key={node.id}
              className={`mechanismGraphNode ${node.type} ${node.id === activeNodeId ? "focused" : ""}`}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
              data-graph-node-id={node.id}
              aria-pressed={node.id === activeNodeId}
              onClick={() => setActiveNodeId(node.id)}
            >
              <span>{node.shortLabel}</span>
            </button>
          ))}
        </section>

        <details className="workspaceDisclosure graphNodeDetailDisclosure">
          <summary>选中节点 · {activeNode?.label || "未选择图谱节点"}</summary>
          <article
            className="mechanismGraphDetail"
            aria-label="selected graph node detail"
            data-graph-focus-node-id={activeNode?.id || ""}
          >
            <div className="workspaceStudioDetailHeader">
              <div>
                <p className="workspaceKicker">选中节点</p>
                <strong>{activeNode?.label || "未选择图谱节点"}</strong>
              </div>
              <WorkspaceStatusPill status={activeNode?.status?.includes("locked") ? "disabled" : "reviewed"}>
                {graphTypeLabel(activeNode?.type)}
              </WorkspaceStatusPill>
            </div>
            <dl className="workspaceStudioDetailFacts">
              <div>
                <dt>节点类型</dt>
                <dd>{graphTypeLabel(activeNode?.type)}</dd>
              </div>
              <div>
                <dt>来源</dt>
                <dd>{focusTarget?.matchedChunkId ? `chunk ${focusTarget.matchedChunkId}` : activeNode?.evidence || "只读图谱摘要"}</dd>
              </div>
              <div>
                <dt>关系候选</dt>
                <dd>{Number(counts.relation_dry_run_candidates || objectSummary.relation_candidate_count || 0)} 条，仅 dry-run</dd>
              </div>
            </dl>
          </article>
          <dl className="workspaceStudioDetailFacts">
            <div>
              <dt>来源笔记</dt>
              <dd>{focusTarget?.serverNoteId || focusTarget?.zoteroAnnotationKey || activeNode?.sourceNote || "未选择"}</dd>
            </div>
            <div>
              <dt>机制状态</dt>
              <dd>{graph.mechanism_generated ? "已生成" : "机制生成未启用"}</dd>
            </div>
          </dl>
          <p>
            {focusTarget
              ? "图谱焦点跟随当前 PDF/evidence 结果；能映射到 evidence、note、object、relation 或 mechanism readiness 时会高亮一跳上下文。"
              : "选择搜索结果或图谱节点后，可查看只读证据、对象、关系 dry-run 与机制 readiness 上下文。"}
          </p>
        </details>
      </section>

      <div className="workspaceStudioToolSurface" hidden={activeTool !== "bridge"}>
        <ManualChatGPTBridgePanel
          documentId={documentId}
          chapterId={chapterId}
          selectionTarget={selectionTarget}
          onOpenReview={handleOpenReview}
        />
      </div>

      <div className="workspaceStudioToolSurface" hidden={activeTool !== "review"}>
        <MechanismDraftReviewPanel
          key={`${reviewHandoff?.packetResult?.review_packet?.packet_id || "empty-review"}-${reviewHandoffRevision}`}
          documentId={documentId}
          chapterId={chapterId}
          initialBundle={reviewHandoff?.inputBundle || null}
          initialPacketResult={reviewHandoff?.packetResult || null}
        />
      </div>

      <details className="workspaceDisclosure graphWorkflowDisclosure">
        <summary>流程状态</summary>
        <WorkflowGraphStatusSummary
          correction={correction}
          classification={classification}
          objectSummary={objectSummary}
          counts={counts}
          graph={graph}
        />
        <section className="mechanismGraphSafetyStrip" aria-label="workflow graph safety strip">
          <span>不保存关系</span>
          <span>不生成机制</span>
          <span>不写 vector</span>
          <span>不写 Zotero</span>
        </section>
      </details>
    </div>
  );
}

function WorkflowGraphStatusSummary({ correction, classification, objectSummary, counts, graph }) {
  return (
    <article className="workspaceStudioDetail reviewed" aria-label="Workflow Studio status retained in graph panel">
      <div className="workspaceStudioDetailHeader">
        <div>
          <p className="workspaceKicker">流程状态</p>
          <strong>只读流程状态</strong>
        </div>
        <WorkspaceStatusPill status="locked" tone="info">Phase7H 未进入</WorkspaceStatusPill>
      </div>
      <dl className="workspaceStudioDetailFacts">
        <div>
          <dt>纠错审核</dt>
          <dd>{Number(correction.saved_items || 0)}/{Number(correction.expected_items || 67)}</dd>
        </div>
        <div>
          <dt>分类审核</dt>
          <dd>{Number(classification.saved_item_count || 0)}/67</dd>
        </div>
        <div>
          <dt>对象草稿</dt>
          <dd>{Number(objectSummary.object_candidate_draft_saved_count || 0)} 已保存</dd>
        </div>
        <div>
          <dt>人工审核</dt>
          <dd>{Number(objectSummary.approved_candidate_count || 0)}/{Number(objectSummary.rejected_candidate_count || 0)}/{Number(objectSummary.pending_candidate_count || 0)}</dd>
        </div>
        <div>
          <dt>关系候选</dt>
          <dd>{Number(counts.relation_dry_run_candidates || objectSummary.relation_candidate_count || 0)} · {objectSummary.relation_validator_valid ? "valid" : "未启用"}</dd>
        </div>
        <div>
          <dt>PN68</dt>
          <dd>{graph.pn68?.quarantined ? "已隔离" : "未隔离"} / {graph.pn68?.excluded_from_relation_dry_run ? "已排除" : "未排除"}</dd>
        </div>
        <div>
          <dt>机制</dt>
          <dd>机制生成未启用 · 既有草稿 {Number(counts.mechanism_draft_candidates || 0)}</dd>
        </div>
        <div>
          <dt>知识关系</dt>
          <dd>{Number(counts.knowledge_relations || 0)} 已保存</dd>
        </div>
      </dl>
      <code>正向关系来源 = 仅已通过对象候选 · rejected/pending/PN68 已排除</code>
    </article>
  );
}

function buildDisplayNodes(graph) {
  const counts = graph.node_counts || {};
  const base = [
    node("evidence_overview", "evidence", "证据", counts.evidence_chunks, "available_read_only"),
    node("note_overview", "note", "笔记", counts.zotero_inspiration_notes || counts.chapter_notes, "reviewed_read_only"),
    node("approved_objects", "object_candidate", "已审核对象", counts.approved_object_candidates, "approved_human_review_read_only"),
    node("relation_dry_run", "relation_candidate", "关系", counts.relation_dry_run_candidates, "future_phase7h_gate_required"),
    node("mechanism_readiness", "mechanism_readiness", "机制", counts.mechanism_draft_candidates, "locked_relations_not_reviewed_phase7h"),
    node("pn68_quarantine", "quarantine", "PN68", graph.pn68?.quarantined ? 1 : 0, "excluded_from_relation_dry_run"),
  ];
  return base;
}

function buildDisplayEdges(graph, nodes) {
  const byId = Object.fromEntries(nodes.map((item) => [item.id, item]));
  return (graph.edges || [])
    .map((edge, index) => {
      const from = byId[edge.source];
      const to = byId[edge.target];
      if (!from || !to) return null;
      return {
        id: edge.id || `edge-${index}`,
        source: edge.source,
        target: edge.target,
        type: edge.type,
        from,
        to,
      };
    })
    .filter(Boolean);
}

function node(id, type, label, count, status) {
  const position = NODE_POSITIONS[id] || { x: 50, y: 50 };
  return {
    id,
    type,
    label,
    count: Number(count || 0),
    status,
    shortLabel: shortLabel(`${label} ${Number(count || 0)}`),
    ...position,
  };
}

function shortLabel(value) {
  const text = String(value || "").trim();
  return text.length > 18 ? `${text.slice(0, 16)}...` : text;
}

function graphTypeLabel(type) {
  if (type === "evidence") return "证据";
  if (type === "note") return "笔记";
  if (type === "object_candidate") return "对象候选";
  if (type === "relation_candidate") return "关系候选";
  if (type === "mechanism_readiness") return "机制就绪";
  if (type === "quarantine") return "隔离项";
  return type || "节点";
}

function studioToolCardClass(active) {
  return active ? "workspaceStudioToolCard active" : "workspaceStudioToolCard";
}

function graphFocusNodeId(target) {
  if (!target) return "";
  if (target.graphFocusNodeId) return target.graphFocusNodeId;
  if (target.objectCandidateId) return "approved_objects";
  if (target.relationTempId) return "relation_dry_run";
  if (target.mechanismId) return "mechanism_readiness";
  if (target.sourceKind === "note" || target.serverNoteId || target.zoteroAnnotationKey) return "note_overview";
  if (target.sourceKind === "relation_evidence") return "relation_dry_run";
  if (target.sourceKind === "object_evidence") return "approved_objects";
  if (target.sourceKind === "passage" || target.matchedChunkId || target.page) return "evidence_overview";
  return "";
}

function edgeHighlights(edge, activeNodeId) {
  return edge.source === activeNodeId || edge.target === activeNodeId;
}

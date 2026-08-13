import {
  readSearchSession,
  summarizeSearchSession,
} from "../../features/retrieval/state/searchSession.js";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

export default function WorkspaceSearchSessionPanel({ onBackToSearch }) {
  const session = summarizeSearchSession(readSearchSession());
  const visibleResults = session.results.slice(0, 4);
  const modeLabel = session.searchKind === "keyword" ? "关键词搜索" : "高质量搜索";
  const currentResult = session.preview;

  return (
    <div
      className="workspacePanelStack searchWorkflowPanel workspaceSearchSessionPanel"
      data-testid="workspace-search-session"
    >
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">统一搜索会话</p>
          <h3>搜索上下文</h3>
          <span>Workspace 读取统一搜索页的结果、Preview 与证据篮子状态。</span>
        </div>
        <div className="workspaceSearchChipRow" aria-label="统一搜索会话状态">
          <WorkspaceStatusPill status="read_only">只读</WorkspaceStatusPill>
          <WorkspaceStatusPill status={session.hasSession ? "available" : "planned"}>
            {session.hasSession ? modeLabel : "尚无会话"}
          </WorkspaceStatusPill>
        </div>
      </div>

      <div className="workspaceSearchBody" data-scroll-region="search-results-no-overlay">
        {!session.hasSession ? (
          <div className="workspaceSearchNoQuery">
            <strong>尚未建立搜索会话</strong>
            <span>返回“搜索”执行检索后，Workspace 会显示同一会话的摘要。</span>
          </div>
        ) : (
          <>
            <div className="workspaceSearchNotice">
              <strong>检索：{session.query || "未输入检索词"}</strong>
              <span>搜索状态、当前结果和证据选择均由统一搜索页维护。</span>
            </div>

            <dl className="workspacePipelineFacts" aria-label="搜索会话摘要">
              <div><dt>模式</dt><dd>{modeLabel}</dd></div>
              <div><dt>结果</dt><dd>{session.resultCount} 条</dd></div>
              <div><dt>证据篮子</dt><dd>{session.basket.length} 条</dd></div>
              <div><dt>Preview</dt><dd>{previewLabel(session.previewStatus, currentResult)}</dd></div>
            </dl>

            {currentResult && (
              <article className="workspaceLayerCard available" aria-label="当前搜索结果">
                <div>
                  <h4>{resultTitle(currentResult)}</h4>
                  <p>{resultLocation(currentResult)}</p>
                </div>
                <WorkspaceStatusPill status="available">当前结果</WorkspaceStatusPill>
              </article>
            )}

            {visibleResults.length > 0 && (
              <section className="workspaceLayerList" aria-label="统一搜索结果摘要">
                {visibleResults.map((result, index) => (
                  <article
                    key={result.fragment_id || result.display_id || index}
                    className="workspaceLayerCard available"
                  >
                    <div>
                      <h4>{resultTitle(result)}</h4>
                      <p>{resultExcerpt(result)}</p>
                    </div>
                  </article>
                ))}
              </section>
            )}
          </>
        )}
      </div>

      <button type="button" className="workspacePillButton secondary" onClick={onBackToSearch}>
        返回搜索查看结果与 PDF Preview
      </button>
    </div>
  );
}

function previewLabel(status, result) {
  if (result) return resultLocation(result);
  if (status === "loading_fragment") return "正在加载";
  if (status === "error") return "加载失败";
  return "未选择";
}

function resultTitle(result) {
  return result?.document_title || result?.title || "未命名来源";
}

function resultLocation(result) {
  const page = result?.pdf_page ?? result?.page_number;
  return page ? `PDF 第 ${page} 页` : "来源位置待确认";
}

function resultExcerpt(result) {
  const text = result?.text || result?.note_text || result?.selected_text || "该结果没有摘要。";
  return String(text).replace(/\s+/g, " ").trim().slice(0, 180);
}

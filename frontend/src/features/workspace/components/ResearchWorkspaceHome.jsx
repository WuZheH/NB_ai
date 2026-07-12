import WorkspaceStatusPill from "../../../components/workspace/WorkspaceStatusPill.jsx";
import {
  buildMachineLearningNotebook,
  openMachineLearningNotebook,
  openSourceWorkspace,
} from "../utils/researchWorkspace.js";

export function NotebookWorkspaceHome({ documentId, homeState, onOpenWorkspace, onOpenImport }) {
  const sources = homeState.sources || [];
  const machineLearningNotebook = buildMachineLearningNotebook(sources, homeState.status);
  return (
    <div className="notebookHomePage fullNotebookHomePage" aria-label="NOTEBOOK_AI 中文笔记本主页">
      <header className="notebookHomeTopbar">
        <div>
          <span className="notebookHomeBrandMark">N</span>
          <div className="notebookHomeBrandStack">
            <strong>NOTEBOOK_AI</strong>
            <h2>本地科研工作台</h2>
            <span>中文笔记本 · 本地资料聚合 · 只读研究入口</span>
          </div>
        </div>
        <div className="notebookHomeToolbar" aria-label="笔记本主页视图控制">
          <span className="notebookHomeSearchShell">搜索笔记本</span>
          <span>网格</span>
          <span>最近</span>
          <button type="button" className="workspacePillButton" onClick={onOpenImport}>
            新建 / 导入
          </button>
        </div>
      </header>

      <nav className="notebookHomeTabs" aria-label="笔记本分类">
        <button type="button" className="selected">全部</button>
        <button type="button">精选笔记本</button>
      </nav>

      {homeState.status === "loading" && <p className="workspaceSampleNotice">来源数量暂不可用，本地 API 可用后自动更新。</p>}
      {homeState.status === "error" && (
        <p className="workspaceSampleNotice warning">来源数量暂不可用，本地 API 可用后自动更新。</p>
      )}

      <section className="notebookHomeSection" aria-label="精选笔记本">
        <div className="notebookHomeSectionHeader">
          <div>
            <p className="workspaceKicker">精选笔记本</p>
            <h3>精选笔记本</h3>
          </div>
          <WorkspaceStatusPill status={homeState.status === "ready" ? "available" : "planned"}>
            {machineLearningNotebook.sourceCountLabel}
          </WorkspaceStatusPill>
        </div>
        <div className="notebookCardGrid featured">
          <NotebookCard
            notebook={machineLearningNotebook}
            featured
            selected={Number(documentId) === DEFAULT_HOME_WORKFLOW_TARGET.documentId}
            accentIndex={0}
            onOpen={() => openMachineLearningNotebook(onOpenWorkspace)}
          />
        </div>
      </section>

      <section className="notebookHomeSection" aria-label="最近打开的笔记本">
        <div className="notebookHomeSectionHeader">
          <div>
            <p className="workspaceKicker">最近打开的笔记本</p>
            <h3>最近打开的笔记本</h3>
          </div>
          <span className="notebookHomeMeta">来源：本地资料库</span>
        </div>
        <div className="notebookCardGrid">
          <NotebookCard
            notebook={machineLearningNotebook}
            selected={Number(documentId) === DEFAULT_HOME_WORKFLOW_TARGET.documentId}
            accentIndex={1}
            onOpen={() => openMachineLearningNotebook(onOpenWorkspace)}
          />
        </div>
      </section>
    </div>
  );
}

export function NotebookCard({ notebook, featured = false, selected = false, accentIndex = 0, onOpen }) {
  const title = notebook.title;
  return (
    <button
      type="button"
      className={`notebookCard ${featured ? "featured" : ""} ${selected ? "selected" : ""}`}
      data-notebook-id={notebook.id}
      onClick={onOpen}
    >
      <span className={`notebookCardCover tone${accentIndex % 4}`}>
        <span>{notebook.coverMark}</span>
      </span>
      <span className="notebookCardBody">
        <strong>{title}</strong>
        <small>{notebook.subtitle}</small>
        <em>{notebook.sourceCountLabel} · 最近打开</em>
        {notebook.warning && <i>{notebook.warning}</i>}
      </span>
      <span className="notebookCardAction">打开</span>
    </button>
  );
}

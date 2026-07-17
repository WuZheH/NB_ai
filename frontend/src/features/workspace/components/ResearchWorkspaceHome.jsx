import WorkspaceStatusPill from "../../../components/workspace/WorkspaceStatusPill.jsx";
import {
  buildWorkspaceNotebooks,
  openSourceWorkspace,
} from "../utils/researchWorkspace.js";

export function NotebookWorkspaceHome({ documentId, homeState, onOpenWorkspace, onOpenImport, onBackToSearch }) {
  const sources = homeState.sources || [];
  const notebooks = buildWorkspaceNotebooks(sources, homeState.status);
  return (
    <div className="notebookHomePage fullNotebookHomePage" aria-label="Search 中文笔记本主页">
      <header className="notebookHomeTopbar">
        <div>
          <span className="notebookHomeBrandMark">N</span>
          <div className="notebookHomeBrandStack">
            <strong>Search</strong>
            <h2>本地科研工作台</h2>
            <span>中文笔记本 · 本地资料聚合 · 只读研究入口</span>
          </div>
        </div>
        <div className="notebookHomeToolbar" aria-label="笔记本主页视图控制">
          <button type="button" className="workspacePillButton secondary" onClick={onBackToSearch}>
            ← 返回搜索
          </button>
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
      </nav>

      {homeState.status === "loading" && <p className="workspaceSampleNotice">来源数量暂不可用，本地 API 可用后自动更新。</p>}
      {homeState.status === "error" && (
        <p className="workspaceSampleNotice warning">来源数量暂不可用，本地 API 可用后自动更新。</p>
      )}

      <section className="notebookHomeSection" aria-label="本地资料">
        <div className="notebookHomeSectionHeader">
          <div>
            <p className="workspaceKicker">资料库</p>
            <h3>本地资料</h3>
          </div>
          <WorkspaceStatusPill status={homeState.status === "ready" ? "available" : "planned"}>
            {homeState.status === "ready" ? `${notebooks.length} 个资料` : "资料状态暂不可用"}
          </WorkspaceStatusPill>
        </div>
        {notebooks.length ? (
          <div className="notebookCardGrid">
            {notebooks.map((notebook, index) => (
              <NotebookCard
                key={notebook.id}
                notebook={notebook}
                selected={Number(documentId) === Number(notebook.source.document_id)}
                accentIndex={index}
                onOpen={() => openSourceWorkspace(notebook.source, onOpenWorkspace)}
              />
            ))}
          </div>
        ) : (
          <div className="researchWorkspaceEmpty">
            <strong>资料库为空</strong>
            <span>请导入 PDF，或在设置中配置数据目录。</span>
            <button type="button" className="workspacePillButton" onClick={onOpenImport}>导入 PDF</button>
          </div>
        )}
      </section>
    </div>
  );
}

export function NotebookCard({ notebook, selected = false, accentIndex = 0, onOpen }) {
  const title = notebook.title;
  return (
    <button
      type="button"
      className={`notebookCard ${selected ? "selected" : ""}`}
      data-notebook-id={notebook.id}
      onClick={onOpen}
    >
      <span className={`notebookCardCover tone${accentIndex % 4}`}>
        <span>{notebook.coverMark}</span>
      </span>
      <span className="notebookCardBody">
        <strong>{title}</strong>
        <small>{notebook.subtitle}</small>
        <em>{notebook.sourceCountLabel}</em>
        {notebook.warning && <i>{notebook.warning}</i>}
      </span>
      <span className="notebookCardAction">打开</span>
    </button>
  );
}

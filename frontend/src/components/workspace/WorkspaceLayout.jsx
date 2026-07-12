export default function WorkspaceLayout({ sourcePanel, workbenchPanel, studioPanel }) {
  return (
    <section className="researchWorkspaceLayout" aria-label="三栏科研搜索工作台">
      <aside className="researchWorkspaceColumn sourceColumn" aria-label="PDF 预览与证据定位">
        {sourcePanel}
      </aside>
      <main className="researchWorkspaceColumn workbenchColumn" aria-label="数据库搜索与结构化检索结果">
        {workbenchPanel}
      </main>
      <aside className="researchWorkspaceColumn studioColumn" aria-label="机制与关系图谱以及工作流 Studio">
        {studioPanel}
      </aside>
    </section>
  );
}

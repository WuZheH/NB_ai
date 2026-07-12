export default function NotebookWorkspaceShell({ children }) {
  return (
    <div className="notebookWorkspaceShell" aria-label="NOTEBOOK_AI notebook workspace shell">
      <main className="notebookWorkspaceSurface">{children}</main>
    </div>
  );
}

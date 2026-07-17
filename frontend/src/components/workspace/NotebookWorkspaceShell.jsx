export default function NotebookWorkspaceShell({ children }) {
  return (
    <div className="notebookWorkspaceShell" aria-label="Search notebook workspace shell">
      <main className="notebookWorkspaceSurface">{children}</main>
    </div>
  );
}

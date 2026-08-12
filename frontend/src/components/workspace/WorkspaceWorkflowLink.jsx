export function advancedWorkflowHref(documentId, chapterId = null) {
  const query = chapterId ? `?chapter=${chapterId}&workflow=notes-import` : "?workflow=notes-import";
  return `/library/books/${documentId}${query}`;
}

export default function WorkspaceWorkflowLink({ documentId, chapterId, onOpenAdvancedWorkflow, children = "打开高级流程" }) {
  const disabled = !documentId;
  const href = disabled ? "#" : advancedWorkflowHref(documentId, chapterId);
  return (
    <a
      className={`workspacePillButton ${disabled ? "disabled" : ""}`}
      href={href}
      onClick={(event) => {
        if (disabled) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        onOpenAdvancedWorkflow?.(documentId, chapterId);
      }}
    >
      {children}
    </a>
  );
}

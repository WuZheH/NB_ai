import { notebookSourceLabel } from "../../features/retrieval/utils/notebookSearch.js";

export default function SourceBadge({ sourceType }) {
  return (
    <span className={`search-source-badge search-source-${sourceType || "unknown"}`}>
      {notebookSourceLabel(sourceType)}
    </span>
  );
}

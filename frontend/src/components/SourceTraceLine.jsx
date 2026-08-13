import { buildTrace } from "../utils/formatters.js";

export default function SourceTraceLine({ trace, fallback = {} }) {
  const normalized = buildTrace(trace, fallback);
  return (
    <div className="sourceLine" aria-label="来源追踪摘要">
      <span>来源追踪</span>
      {normalized.document_id && <em>doc {normalized.document_id}</em>}
      {normalized.chunk_id && <em>chunk {normalized.chunk_id}</em>}
      {normalized.pdf_page && <em>p. {normalized.pdf_page}</em>}
      {normalized.zotero_key && <em>{normalized.zotero_key}</em>}
    </div>
  );
}

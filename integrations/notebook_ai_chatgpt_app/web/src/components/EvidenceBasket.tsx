import type { EvidenceFormat, SearchResult } from "../types";

interface EvidenceBasketProps {
  selected: SearchResult[];
  exporting: boolean;
  status: string;
  onClear: () => void;
  onExport: (format: EvidenceFormat) => void;
}

export function EvidenceBasket({ selected, exporting, status, onClear, onExport }: EvidenceBasketProps) {
  return (
    <aside className="evidence-basket" aria-label="证据选择篮子">
      <div>
        <strong>证据篮子</strong>
        <span>{selected.length} 条</span>
      </div>
      {selected.length > 0 && (
        <ol>
          {selected.map((result) => (
            <li key={result.fragment_id}>{result.document_title || result.fragment_id}</li>
          ))}
        </ol>
      )}
      <div className="basket-actions">
        <button type="button" disabled={!selected.length || exporting} onClick={() => onExport("markdown")}>
          复制 Markdown
        </button>
        <button type="button" disabled={!selected.length || exporting} onClick={() => onExport("jsonl")}>
          导出 JSONL
        </button>
        <button type="button" disabled={!selected.length || exporting} onClick={() => onExport("json")}>
          导出 JSON
        </button>
        <button type="button" disabled={!selected.length || exporting} onClick={onClear}>清空</button>
      </div>
      {status && <p className="basket-status" role="status">{status}</p>}
    </aside>
  );
}

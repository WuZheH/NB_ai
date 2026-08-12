import type { EvidenceFormat, SearchResult } from "../types";

interface EvidenceBasketProps {
  selected: SearchResult[];
  selectedCount: number;
  canPin: boolean;
  exporting: boolean;
  status: string;
  onClear: () => void;
  onExport: (format: EvidenceFormat) => void;
  onPin: () => void;
}

export function EvidenceBasket({ selected, selectedCount, canPin, exporting, status, onClear, onExport, onPin }: EvidenceBasketProps) {
  if (selected.length === 0) return null;

  return (
    <aside className="evidence-basket" aria-label="证据选择篮子">
      <div>
        <strong>证据篮子</strong>
        <span>{selectedCount} 条</span>
      </div>
      <ol aria-label="已选证据列表">
        {selected.map((result) => (
          <li key={result.fragment_id}>{result.document_title || result.fragment_id}</li>
        ))}
      </ol>
      <div className="basket-actions">
        <button type="button" className="search-button search-button-primary search-button-compact" disabled={!canPin || exporting} onClick={onPin}>
          固定选择到聊天
        </button>
        <button type="button" className="search-button search-button-subtle search-button-compact" disabled={!selectedCount || exporting} onClick={() => onExport("markdown")}>
          复制 Markdown
        </button>
        <details className="search-overflow-menu basket-more-menu">
          <summary className="search-menu-button search-button-compact" aria-label="更多证据操作">更多</summary>
          <div className="search-overflow-menu-panel">
            <button type="button" className="search-button search-button-transparent search-button-compact" disabled={!selectedCount || exporting} onClick={() => onExport("jsonl")}>导出 JSONL</button>
            <button type="button" className="search-button search-button-transparent search-button-compact" disabled={!selectedCount || exporting} onClick={() => onExport("json")}>导出 JSON</button>
            <button type="button" className="search-button search-button-transparent search-button-compact" disabled={!selectedCount || exporting} onClick={onClear}>清空</button>
          </div>
        </details>
      </div>
      {status && <p className="basket-status" role="status">{status}</p>}
    </aside>
  );
}

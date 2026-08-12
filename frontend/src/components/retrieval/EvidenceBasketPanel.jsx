import { useMemo } from "react";
import { notebookSourceLabel } from "../../features/retrieval/utils/notebookSearch.js";
import EvidenceExportDialog from "./EvidenceExportDialog.jsx";

export default function EvidenceBasketPanel({
  items,
  exportOptions,
  exportBusy,
  notice,
  error,
  onRemove,
  onClear,
  onMove,
  onExportOptionsChange,
  onExport,
}) {
  const summary = useMemo(() => {
    const sourceTypes = new Set();
    const documents = new Set();
    items.forEach((item) => {
      sourceTypes.add(item.source_type);
      documents.add(item.document_id ? `document:${item.document_id}` : `fragment:${item.fragment_id}`);
    });
    return { sourceTypes: sourceTypes.size, documents: documents.size };
  }, [items]);

  return (
    <aside className="localEvidenceBasket" aria-label="证据篮子" data-testid="evidence-basket">
      <div className="localEvidenceBasketHeader">
        <div>
          <span>证据篮子</span>
          <strong>{items.length} 条证据</strong>
          <small>{summary.documents} 个文档 · {summary.sourceTypes} 类来源</small>
        </div>
        <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onClear} disabled={!items.length}>清空</button>
      </div>

      <div
        className="localEvidenceBasketList search-scroll-region"
        data-testid="evidence-basket-scroll"
        tabIndex={0}
        role="region"
        aria-label="可滚动的证据篮子列表"
      >
        {!items.length && <p className="localRetrievalState">尚未选择证据。</p>}
        {items.map((item, index) => (
          <div className="localEvidenceBasketItem" key={item.fragment_id}>
            <div className="localEvidenceBasketOrder">E{String(index + 1).padStart(3, "0")}</div>
            <div className="localEvidenceBasketMain">
              <strong>{item.title || "未命名来源"}</strong>
              <span>{notebookSourceLabel(item.source_type)} · {item.page_label || item.page_number || "无页码"}</span>
              <small className="search-mono">{shortFragmentId(item.fragment_id)}</small>
            </div>
            <div className="localEvidenceBasketControls">
              <button type="button" className="search-icon-button search-button-compact" aria-label="上移" title="上移" disabled={index === 0} onClick={() => onMove(index, -1)}>↑</button>
              <button type="button" className="search-icon-button search-button-compact" aria-label="下移" title="下移" disabled={index === items.length - 1} onClick={() => onMove(index, 1)}>↓</button>
              <button type="button" className="search-icon-button search-button-compact search-button-danger" aria-label="移除" title="移除" onClick={() => onRemove(item.fragment_id)}>×</button>
            </div>
          </div>
        ))}
      </div>

      <EvidenceExportDialog
        options={exportOptions}
        busy={exportBusy}
        disabled={!items.length}
        onChange={onExportOptionsChange}
        onExport={onExport}
      />
      <div className="localEvidenceBasketStatus" aria-live="polite">
        {notice && <p>{notice}</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </aside>
  );
}

function shortFragmentId(value) {
  const fragmentId = String(value || "");
  return fragmentId.length > 18 ? `${fragmentId.slice(0, 8)}…${fragmentId.slice(-6)}` : fragmentId;
}

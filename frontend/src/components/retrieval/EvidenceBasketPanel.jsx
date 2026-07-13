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
    <aside className="localEvidenceBasket" aria-label="Evidence Basket">
      <div className="localEvidenceBasketHeader">
        <div>
          <span>Evidence Basket</span>
          <strong>{items.length} 条证据</strong>
          <small>{summary.documents} 个文档 · {summary.sourceTypes} 类来源</small>
        </div>
        <button type="button" className="quietButton" onClick={onClear} disabled={!items.length}>清空</button>
      </div>

      <div className="localEvidenceBasketList">
        {!items.length && <p className="localRetrievalState">尚未选择证据。</p>}
        {items.map((item, index) => (
          <div className="localEvidenceBasketItem" key={item.fragment_id}>
            <div className="localEvidenceBasketOrder">E{String(index + 1).padStart(3, "0")}</div>
            <div className="localEvidenceBasketMain">
              <strong>{item.display_id}</strong>
              <span>{item.title || "未命名来源"}</span>
              <small>{notebookSourceLabel(item.source_type)} · {item.page_label || item.page_number || "无页码"}</small>
            </div>
            <div className="localEvidenceBasketControls">
              <button type="button" aria-label="上移" title="上移" disabled={index === 0} onClick={() => onMove(index, -1)}>↑</button>
              <button type="button" aria-label="下移" title="下移" disabled={index === items.length - 1} onClick={() => onMove(index, 1)}>↓</button>
              <button type="button" aria-label="移除" title="移除" onClick={() => onRemove(item.fragment_id)}>×</button>
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

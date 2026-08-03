import {
  zoteroPdfDuplicate,
  zoteroPdfExistingSummary,
  zoteroPdfImported,
  zoteroPdfImportStatus,
  zoteroPdfMatchReasonSummary,
} from "./zoteroPdfImportStatus.js";

export default function ZoteroPdfResultCard({ source, selected, compact, onSelect, onOpenDocument, onRecheck }) {
  const title = zoteroPdfSourceTitle(source);
  const authorYear = zoteroPdfAuthorYear(source);
  const itemKey = zoteroPdfItemKey(source);
  const attachmentKey = zoteroPdfAttachmentKey(source);
  const annotationCount = zoteroPdfAnnotationCount(source);
  const userNoteCount = zoteroPdfUserNoteCount(source);
  const evidenceOnlyCount = zoteroPdfEvidenceOnlyCount(source);
  const pdfPath = zoteroPdfPath(source);
  const available = zoteroPdfAvailable(source);
  const importStatus = zoteroPdfImportStatus(source);
  const imported = importStatus.imported;
  const existingSummary = zoteroPdfExistingSummary(importStatus);
  const matchSummary = zoteroPdfMatchReasonSummary(importStatus);

  return (
    <article className={`zoteroPdfResultCard ${compact ? "compact" : "list"} ${selected ? "selected" : ""}`}>
      <div className="zoteroPdfCardHeader">
        <div>
          <strong>{title}</strong>
          <span>{authorYear}</span>
        </div>
        <div className="zoteroPdfStatusChips" aria-label="status chips">
          <span className={`zoteroImportStatusBadge ${importStatus.status}`}>{importStatus.label}</span>
          {selected && <span>已选择</span>}
        </div>
      </div>

      <div className="zoteroPdfReadableMeta">
        <div><span>Zotero 条目</span><strong>{itemKey || "n/a"}</strong></div>
        <div><span>PDF 附件</span><strong>{attachmentKey || "n/a"}</strong></div>
        <div><span>笔记统计</span><strong>{annotationCount} annotations / {userNoteCount} user notes / {evidenceOnlyCount} evidence-only</strong></div>
        <div className="zoteroPdfReadablePath"><span>PDF 路径</span><strong title={pdfPath || ""}>{pdfPath || "未记录路径"}</strong></div>
      </div>

      {(existingSummary || matchSummary) && (
        <div className={`zoteroPdfHumanStatus ${importStatus.status}`}>
          {existingSummary && <strong>{existingSummary}</strong>}
          <span>{matchSummary}</span>
        </div>
      )}

      {importStatus.status === "sibling_imported" && (
        <div className="zoteroPdfImportNotice">
          同一 Zotero 条目已有入库文档。默认先查看已有文档；如确需处理当前 attachment，可使用“仍选择当前 PDF”。
        </div>
      )}

      {source.__sameZoteroItem && (
        <div className="zoteroPdfMultiAttachmentNotice">
          同一 Zotero item · 不同 attachment
          {source.__annotationCountsDiffer ? " · annotation 数量不同" : " · annotation 数量可能不同"}
        </div>
      )}

      <div className="previewActions">
        {importStatus.status === "exact_imported" ? (
          <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(importStatus.existingDocumentId)} disabled={!importStatus.existingDocumentId}>
            打开已有文档
          </button>
        ) : importStatus.status === "sibling_imported" ? (
          <>
            <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(importStatus.existingDocumentId)} disabled={!importStatus.existingDocumentId}>
              查看已有文档
            </button>
            <button type="button" className="quietButton" onClick={() => onSelect(source)} disabled={!available}>
              仍选择当前 PDF
            </button>
          </>
        ) : importStatus.status === "unknown" ? (
          <button type="button" onClick={onRecheck} disabled={!onRecheck}>
            重新检查状态
          </button>
        ) : imported ? (
          <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(importStatus.existingDocumentId)} disabled={!importStatus.existingDocumentId}>
            打开已有文档
          </button>
        ) : (
          <button type="button" className="primaryButton" onClick={() => onSelect(source)} disabled={!available}>
            选择该 PDF
          </button>
        )}
      </div>
    </article>
  );
}

export function buildZoteroPdfDisplaySources(sources, sortMode, filterMode) {
  const itemCounts = new Map();
  const itemAnnotationCounts = new Map();
  const prepared = (sources || []).map((source, index) => {
    const itemKey = zoteroPdfItemKey(source);
    if (itemKey) {
      itemCounts.set(itemKey, (itemCounts.get(itemKey) || 0) + 1);
      const counts = itemAnnotationCounts.get(itemKey) || new Set();
      counts.add(zoteroPdfAnnotationCount(source));
      itemAnnotationCounts.set(itemKey, counts);
    }
    return { ...source, __sourceIndex: index };
  }).map(source => {
    const itemKey = zoteroPdfItemKey(source);
    const sameItemCount = itemKey ? itemCounts.get(itemKey) || 0 : 0;
    return {
      ...source,
      __sameZoteroItem: sameItemCount > 1,
      __annotationCountsDiffer: itemKey ? (itemAnnotationCounts.get(itemKey)?.size || 0) > 1 : false,
    };
  });

  return prepared
    .filter(source => zoteroPdfSourcePassesFilter(source, filterMode))
    .sort((a, b) => compareZoteroPdfSources(a, b, sortMode));
}

export function zoteroPdfSourceIdentity(source = {}) {
  return source.id ?? source.zotero_attachment_key ?? source.attachment_key ?? source.resolved_pdf_path ?? source.pdf_path ?? source.__sourceIndex;
}

export function sameZoteroPdfSource(a = {}, b = {}) {
  if (!a || !b) return false;
  return String(zoteroPdfSourceIdentity(a)) === String(zoteroPdfSourceIdentity(b));
}

export function zoteroPdfSourceTitle(source = {}) {
  return source.title || source.item_title || source.document_title || "Untitled PDF";
}

export function zoteroPdfAnnotationCount(source = {}) {
  return numberValue(source.annotation_count ?? source.annotations_count ?? source.native_annotation_count ?? source.annotationCount);
}

export function zoteroPdfUserNoteCount(source = {}) {
  return numberValue(source.user_note_count ?? source.user_notes_count ?? source.native_note_count ?? source.note_count);
}

function compareZoteroPdfSources(a, b, sortMode) {
  const titleCompare = zoteroPdfSourceTitle(a).localeCompare(zoteroPdfSourceTitle(b), "zh-Hans-CN", { sensitivity: "base" });
  if (sortMode === "annotations") {
    return zoteroPdfAnnotationCount(b) - zoteroPdfAnnotationCount(a) || titleCompare;
  }
  if (sortMode === "unimported") {
    return Number(zoteroPdfImported(a)) - Number(zoteroPdfImported(b)) || titleCompare;
  }
  if (sortMode === "recent") {
    return zoteroPdfDateScore(b) - zoteroPdfDateScore(a) || titleCompare;
  }
  if (sortMode === "title") {
    return titleCompare;
  }
  return (
    Number(zoteroPdfUserNoteCount(b) > 0) - Number(zoteroPdfUserNoteCount(a) > 0) ||
    Number(zoteroPdfAnnotationCount(b) > 0) - Number(zoteroPdfAnnotationCount(a) > 0) ||
    Number(zoteroPdfImported(a)) - Number(zoteroPdfImported(b)) ||
    titleCompare
  );
}

function zoteroPdfSourcePassesFilter(source, filterMode) {
  if (filterMode === "with_annotations") return zoteroPdfAnnotationCount(source) > 0;
  if (filterMode === "with_user_notes") return zoteroPdfUserNoteCount(source) > 0;
  if (filterMode === "unimported") return !zoteroPdfImported(source);
  if (filterMode === "imported") return zoteroPdfImported(source);
  if (filterMode === "duplicates") return zoteroPdfDuplicate(source) || source.__sameZoteroItem;
  return true;
}

function zoteroPdfAuthorYear(source = {}) {
  const creators = zoteroPdfCreators(source);
  const year = source.year || source.date_year || source.publication_year || "";
  return [creators || "无作者信息", year].filter(Boolean).join(" / ");
}

function zoteroPdfCreators(source = {}) {
  const creators = source.creators || source.authors || source.creator_names || [];
  if (typeof creators === "string") return creators;
  if (!Array.isArray(creators)) return "";
  return creators.slice(0, 4).map(creator => {
    if (typeof creator === "string") return creator;
    return creator.name || [creator.firstName || creator.first_name, creator.lastName || creator.last_name].filter(Boolean).join(" ");
  }).filter(Boolean).join(", ");
}

function zoteroPdfItemKey(source = {}) {
  return source.zotero_item_key || source.item_key || source.parent_item_key || "";
}

function zoteroPdfAttachmentKey(source = {}) {
  return source.zotero_attachment_key || source.attachment_key || "";
}

function zoteroPdfEvidenceOnlyCount(source = {}) {
  return numberValue(source.evidence_only_count ?? source.evidence_only_note_count ?? source.evidence_count);
}

function zoteroPdfPath(source = {}) {
  return source.resolved_pdf_path || source.pdf_path || source.path || source.attachment_path || "";
}

function zoteroPdfAvailable(source = {}) {
  return source.path_exists !== false && source.cache_status !== "missing" && source.import_status !== "missing_file";
}

function zoteroPdfDateScore(source = {}) {
  const raw = source.date_added || source.added_at || source.created_at || source.updated_at || source.last_seen_at || "";
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function numberValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

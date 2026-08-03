import { getActionPageInfo, getPdfFallbackAction, getPreviewAction, getZoteroReadAction, pdfActionUnavailableReason } from "../utils/formatters.js";

export default function PdfActionGroup({
  source = {},
  onPreview,
  showPreview = false,
  showUnavailable = true,
  compact = false,
}) {
  const previewAction = showPreview ? getPreviewAction(source) : null;
  const zoteroAction = getZoteroReadAction(source);
  const fallbackAction = getPdfFallbackAction(source);
  const externalAction = zoteroAction || fallbackAction;
  const hasAction = Boolean((previewAction && onPreview) || externalAction);
  const pageInfo = getActionPageInfo(source);

  if (!hasAction && !showUnavailable) return null;

  return (
    <div className={`pdfActionGroup ${compact ? "compact" : ""}`}>
      {previewAction && onPreview && (
        <button type="button" className="pdfActionButton" onClick={() => onPreview(previewAction)}>
          {previewAction.label}
        </button>
      )}
      {externalAction && (
        <a className="pdfAction" href={externalAction.href} target="_blank" rel="noreferrer">
          {externalAction.label}
        </a>
      )}
      {!hasAction && (
        <span className="pdfUnavailable">{pdfActionUnavailableReason(source)}</span>
      )}
      {pageInfo && hasAction && (
        <span className="pdfActionPageHint">{pageInfo}</span>
      )}
    </div>
  );
}

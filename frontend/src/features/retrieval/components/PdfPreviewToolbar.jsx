export default function PdfPreviewToolbar({ pageNumber, pageCount, scale, onPrevious, onNext, onZoomOut, onZoomIn, onRotate, onFitWidth, canPrevious, canNext }) {
  return (
    <div className="searchPdfToolbar" aria-label="PDF 预览工具栏">
      <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onPrevious} disabled={!canPrevious}>上一页</button>
      <span className="searchPdfPageCounter" aria-label={`第 ${pageNumber || 0} 页，共 ${pageCount || 0} 页`}>
        {pageNumber || "–"} / {pageCount || "–"}
      </span>
      <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onNext} disabled={!canNext}>下一页</button>
      <span className="searchPdfToolbarSpacer" />
      <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onZoomOut} aria-label="缩小 PDF">−</button>
      <span className="searchPdfZoom">{Math.round(scale * 100)}%</span>
      <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onZoomIn} aria-label="放大 PDF">+</button>
      <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onRotate} aria-label="旋转 PDF">旋转</button>
      <button type="button" className="search-button search-button-subtle search-button-compact" onClick={onFitWidth}>适合宽度</button>
    </div>
  );
}

export default function PdfHighlightOverlay({ rects = [], strategy }) {
  if (!rects.length) return null;
  return (
    <div className="searchPdfHighlightLayer" aria-hidden="true" data-testid="pdf-highlight-layer" data-strategy={strategy || "none"}>
      {rects.map((rect, index) => (
        <span
          className="searchPdfHighlight"
          data-testid="pdf-highlight-rect"
          key={`${rect.left.toFixed(2)}:${rect.top.toFixed(2)}:${rect.width.toFixed(2)}:${rect.height.toFixed(2)}:${index}`}
          style={{ left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` }}
        />
      ))}
    </div>
  );
}

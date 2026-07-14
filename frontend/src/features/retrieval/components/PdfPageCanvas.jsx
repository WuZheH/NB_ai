import { forwardRef } from "react";
import PdfHighlightOverlay from "./PdfHighlightOverlay.jsx";

const PdfPageCanvas = forwardRef(function PdfPageCanvas({ width, height, highlights, highlightStrategy }, ref) {
  return (
    <div className="searchPdfPage" style={{ width: `${width}px`, height: `${height}px` }} data-testid="pdf-page-wrap">
      <canvas ref={ref} className="searchPdfCanvas" data-testid="pdf-page-canvas" />
      <PdfHighlightOverlay rects={highlights} strategy={highlightStrategy} />
    </div>
  );
});

export default PdfPageCanvas;

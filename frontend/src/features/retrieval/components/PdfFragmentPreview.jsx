import { useEffect, useMemo, useRef, useState } from "react";
import PdfPageCanvas from "./PdfPageCanvas.jsx";
import PdfPreviewToolbar from "./PdfPreviewToolbar.jsx";
import {
  matchPdfTextItems,
  pdfTextItemRects,
  safePdfEndpoint,
  viewportRectFromPdfRect,
} from "./pdfHighlight.js";

const DEFAULT_SCALE = 1.2;
const MIN_SCALE = 0.65;
const MAX_SCALE = 3;
const SCALE_STEP = 0.15;
let pdfjsPromise;

export default function PdfFragmentPreview({ fragment, locator, onUnavailable }) {
  const canvasRef = useRef(null);
  const scrollerRef = useRef(null);
  const documentRef = useRef(null);
  const loadingTaskRef = useRef(null);
  const renderTaskRef = useRef(null);
  const requestIdRef = useRef(0);
  const unavailableRef = useRef(onUnavailable);
  const [pdfDocument, setPdfDocument] = useState(null);
  const [pageNumber, setPageNumber] = useState(null);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(DEFAULT_SCALE);
  const [rotation, setRotation] = useState(0);
  const [renderState, setRenderState] = useState({ status: "loading_pdf", width: 0, height: 0, highlights: [], strategy: "none", message: "正在加载 PDF" });

  const pdfUrl = useMemo(() => safePdfEndpoint(locator?.pdf_endpoint || fragment?.open_target?.pdf_url), [fragment?.open_target?.pdf_url, locator?.pdf_endpoint]);
  const targetPage = Number(locator?.pdf_page || fragment?.pdf_page || 0) || null;
  const selectedText = locator?.selected_text || fragment?.selected_text || (fragment?.source_type === "pdf_chunk" ? fragment?.text : null);

  useEffect(() => {
    unavailableRef.current = onUnavailable;
  }, [onUnavailable]);

  useEffect(() => {
    requestIdRef.current += 1;
    const requestId = requestIdRef.current;
    renderTaskRef.current?.cancel?.();
    loadingTaskRef.current?.destroy?.();
    documentRef.current?.destroy?.();
    documentRef.current = null;
    loadingTaskRef.current = null;
    setPdfDocument(null);
    setPageCount(0);
    setPageNumber(targetPage);
    setScale(DEFAULT_SCALE);
    setRotation(0);
    if (!pdfUrl || !locator?.pdf_available) {
      setRenderState({ status: "pdf_unavailable", width: 0, height: 0, highlights: [], strategy: "none", message: "此片段没有可用 PDF" });
      return undefined;
    }
    let disposed = false;
    setRenderState({ status: "loading_pdf", width: 0, height: 0, highlights: [], strategy: "none", message: "正在加载 PDF" });
    loadPdfJs().then((pdfjsLib) => {
      if (disposed || requestId !== requestIdRef.current) return null;
      const task = pdfjsLib.getDocument({ url: pdfUrl, withCredentials: false });
      loadingTaskRef.current = task;
      return task.promise;
    }).then((pdf) => {
      if (!pdf || disposed || requestId !== requestIdRef.current) {
        pdf?.destroy?.();
        return;
      }
      documentRef.current = pdf;
      setPdfDocument(pdf);
      setPageCount(pdf.numPages);
      setPageNumber(clampPage(targetPage || 1, pdf.numPages));
    }).catch((error) => {
      if (disposed || requestId !== requestIdRef.current) return;
      const status = /404|not found/iu.test(String(error?.message || "")) ? "pdf_file_missing" : "pdf_load_failed";
      console.warn("[Search PDF preview] load failed", status, String(error?.message || error?.name || "unknown"));
      setRenderState({ status, width: 0, height: 0, highlights: [], strategy: "none", message: status === "pdf_file_missing" ? "PDF 文件不存在" : "PDF 加载失败，已切换到文本预览" });
      unavailableRef.current?.(status);
    });
    return () => {
      disposed = true;
      loadingTaskRef.current?.destroy?.();
      documentRef.current?.destroy?.();
      documentRef.current = null;
    };
  }, [fragment?.fragment_id, locator?.pdf_available, pdfUrl, targetPage]);

  useEffect(() => {
    if (!pdfDocument || !pageNumber) return undefined;
    const requestId = requestIdRef.current;
    let cancelled = false;
    renderTaskRef.current?.cancel?.();
    setRenderState((current) => ({ ...current, status: "rendering_page", highlights: [], strategy: "none", message: "正在渲染目标页" }));
    async function renderPage() {
      const pdfjsLib = await loadPdfJs();
      const page = await pdfDocument.getPage(pageNumber);
      if (cancelled || requestId !== requestIdRef.current) return;
      const viewport = page.getViewport({ scale, rotation });
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) throw new Error("pdf_canvas_unavailable");
      const outputScale = Math.min(2, Number(window.devicePixelRatio || 1));
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const task = page.render({ canvasContext: context, viewport, transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0] });
      renderTaskRef.current = task;
      await task.promise;
      if (cancelled || requestId !== requestIdRef.current) return;
      const bboxRects = pageNumber === targetPage
        ? (locator?.rects || []).map((rect) => viewportRectFromPdfRect(viewport, rect)).filter(Boolean)
        : [];
      let highlights = bboxRects;
      let strategy = bboxRects.length ? "bbox" : "none";
      if (!highlights.length && pageNumber === targetPage && selectedText) {
        const textContent = await page.getTextContent();
        const match = matchPdfTextItems(textContent.items, selectedText);
        if (match.matched) {
          highlights = pdfTextItemRects(textContent.items, match.itemIndexes, viewport, pdfjsLib);
          strategy = highlights.length ? "text" : "none";
        }
      }
      const status = highlights.length ? (strategy === "bbox" ? "bbox_highlighted" : "text_highlighted") : "page_only";
      const message = status === "bbox_highlighted"
        ? "已使用区域坐标高光"
        : status === "text_highlighted"
          ? "已匹配文本并高光"
          : "已打开目标页，但未能精确高光";
      setRenderState({ status, width: viewport.width, height: viewport.height, highlights, strategy, message });
    }
    renderPage().catch((error) => {
      if (cancelled || requestId !== requestIdRef.current || error?.name === "RenderingCancelledException") return;
      console.warn("[Search PDF preview] render failed", String(error?.message || error?.name || "unknown"));
      setRenderState({ status: "pdf_load_failed", width: 0, height: 0, highlights: [], strategy: "none", message: "PDF 页面渲染失败，已切换到文本预览" });
      unavailableRef.current?.("pdf_load_failed");
    });
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel?.();
    };
  }, [locator?.rects, pageNumber, pdfDocument, rotation, scale, selectedText, targetPage]);

  function fitWidth() {
    if (!pdfDocument || !pageNumber || !scrollerRef.current) return;
    pdfDocument.getPage(pageNumber).then((page) => {
      const base = page.getViewport({ scale: 1, rotation });
      const width = Math.max(160, scrollerRef.current.clientWidth - 28);
      setScale(clampScale(width / base.width));
    }).catch(() => {});
  }

  return (
    <section className="searchPdfPreview" data-testid="pdf-fragment-preview" data-pdf-rotation={rotation} aria-label="内嵌 PDF 预览">
      <PdfPreviewToolbar
        pageNumber={pageNumber}
        pageCount={pageCount}
        scale={scale}
        canPrevious={Boolean(pageNumber > 1)}
        canNext={Boolean(pageNumber && pageNumber < pageCount)}
        onPrevious={() => setPageNumber((value) => Math.max(1, Number(value || 1) - 1))}
        onNext={() => setPageNumber((value) => Math.min(pageCount, Number(value || 1) + 1))}
        onZoomOut={() => setScale((value) => clampScale(value - SCALE_STEP))}
        onZoomIn={() => setScale((value) => clampScale(value + SCALE_STEP))}
        onRotate={() => setRotation((value) => (value + 90) % 360)}
        onFitWidth={fitWidth}
      />
      <p className="searchPdfStatus" role="status" data-status={renderState.status}>{renderState.message}</p>
      <div className="searchPdfScroller" ref={scrollerRef} tabIndex={0} aria-label="可滚动的 PDF 页面">
        {pdfDocument && pageNumber && (
          <PdfPageCanvas ref={canvasRef} width={renderState.width || 1} height={renderState.height || 1} highlights={renderState.highlights} highlightStrategy={renderState.strategy} />
        )}
      </div>
    </section>
  );
}

async function loadPdfJs() {
  if (!pdfjsPromise) {
    pdfjsPromise = Promise.all([
      // The legacy bundle carries the small compatibility shims required by
      // the Electron renderer shipped with Search (including Uint8Array#toHex
      // inside the worker realm).  It remains a local Vite asset.
      import("pdfjs-dist/legacy/build/pdf.mjs"),
      import("pdfjs-dist/legacy/build/pdf.worker.mjs?url"),
    ]).then(([pdfjsLib, workerModule]) => {
      pdfjsLib.GlobalWorkerOptions.workerSrc = workerModule.default;
      return pdfjsLib;
    });
  }
  return pdfjsPromise;
}

function clampPage(value, pageCount) {
  return Math.max(1, Math.min(pageCount, Number(value) || 1));
}

function clampScale(value) {
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, Number(value.toFixed(2))));
}

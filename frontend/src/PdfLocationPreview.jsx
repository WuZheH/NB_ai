import { useEffect, useMemo, useRef, useState } from "react";
import {
  calculateHighlightScroll,
  focusToHighlightUnion,
  isRenderReadyForFocus,
  shouldApplyAutoFocus
} from "./utils/pdfFocus.js";
import { cleanSearchSnippet } from "./utils/snippet.js";

const DEFAULT_SCALE = 1.35;
const MIN_SCALE = 0.75;
const FOCUS_MIN_SCALE = 1;
const MAX_SCALE = 3.5;
const SCALE_STEP = 0.15;
let pdfjsLoadPromise;

export function buildDocumentPdfPath(documentId) {
  if (!documentId) return "";
  return `/api/v1/library/documents/${encodeURIComponent(documentId)}/pdf`;
}

export function stripHash(url = "") {
  return String(url || "").split("#")[0];
}

export function extractPage(url = "") {
  const match = String(url || "").match(/[#&?]page=(\d+)/i);
  return match ? normalizePage(match[1]) : null;
}

function PdfLocationPreview({
  apiBase,
  documentId,
  location,
  pdfUrl,
  page,
  pageNumber,
  pdf_page_start,
  pdf_page_end,
  chunkId,
  quote,
  highlightText
}) {
  const canvasRef = useRef(null);
  const scrollerRef = useRef(null);
  const autoFocusKeyRef = useRef("");
  const manualZoomKeyRef = useRef("");
  const pendingFocusRef = useRef(null);
  const [scale, setScale] = useState(DEFAULT_SCALE);
  const [renderState, setRenderState] = useState({
    status: "idle",
    width: 0,
    height: 0,
    baseWidth: 0,
    baseHeight: 0,
    pageNumber: null,
    scale: null,
    outputScale: 1,
    backingWidth: 0,
    backingHeight: 0,
    errorTitle: "",
    errorMessage: ""
  });

  const shouldShowHighlights = ["exact_text_location", "layout_line_location", "layout_sentence_location", "layout_block_location", "layout_bbox_location", "chunk_aligned", "partial_chunk_aligned", "fallback_term_found"].includes(location?.locator_status)
    || location?.status === "located";
  const rects = shouldShowHighlights && Array.isArray(location?.rects) ? location.rects : [];
  const highlightCount = location?.highlight_count ?? rects.length;
  const isApproximateRegion = isApproximateLocation(location);
  const isLayoutRegion = isLayoutLocation(location);
  const statusLabel = pdfLocationStatusLabel(location, highlightCount);
  const countLabel = pdfHighlightCountLabel(location, highlightCount);
  const noticeText = cleanSearchSnippet(highlightText || quote || location?.snippet_used || "");
  const rawPdfUrl = pdfUrl || buildDocumentPdfPath(documentId) || (apiBase && documentId ? `${apiBase}/api/v1/library/documents/${documentId}/pdf` : "");
  const resolvedPdfUrl = normalizePdfJsUrl(rawPdfUrl);
  const pdfPage = normalizePage(location?.pdf_page ?? page ?? pageNumber ?? extractPage(rawPdfUrl) ?? pdf_page_start ?? pdf_page_end);
  const pageWidth = location?.page_width || renderState.baseWidth || renderState.width;
  const pageHeight = location?.page_height || renderState.baseHeight || renderState.height;
  const zoomLabel = `${Math.round(scale * 100)}%`;
  const canvasSizeStyle = renderState.width && renderState.height
    ? { width: `${renderState.width}px`, height: `${renderState.height}px` }
    : undefined;
  const emptyReason = !resolvedPdfUrl
    ? "缺少 PDF URL"
    : !pdfPage
      ? "缺少 PDF 页码"
      : "";

  const canFocusHighlight = shouldShowHighlights && rects.length > 0 && !isApproximateRegion;
  const focusMode = isLayoutRegion ? "layout" : "exact";
  const focusKey = useMemo(() => {
    if (!canFocusHighlight || !pdfPage) return "";
    const rectKey = rects
      .map((rect) => `${rect.x0},${rect.y0},${rect.x1},${rect.y1}`)
      .join("|");
    return [
      chunkId || "",
      pdfPage || "",
      location?.locator_status || location?.status || "",
      location?.visual_mode || "",
      rectKey
    ].join(":");
  }, [canFocusHighlight, chunkId, pdfPage, location?.locator_status, location?.status, location?.visual_mode, rects]);

  useEffect(() => {
    if (pendingFocusRef.current && pendingFocusRef.current.key !== focusKey) {
      pendingFocusRef.current = null;
    }
  }, [focusKey]);

  useEffect(() => {
    if (!resolvedPdfUrl || !pdfPage) {
      setRenderState({ status: "idle", width: 0, height: 0, baseWidth: 0, baseHeight: 0, pageNumber: null, scale: null, outputScale: 1, backingWidth: 0, backingHeight: 0, errorTitle: "", errorMessage: "" });
      return undefined;
    }

    let cancelled = false;
    let loadingTask;

    function fail(stage, error) {
      const diagnostic = pdfPreviewError(stage, error);
      console.error("[PDF preview]", diagnostic.consoleMessage);
      if (!cancelled) {
        setRenderState({
          status: "error",
          width: 0,
          height: 0,
          baseWidth: 0,
          baseHeight: 0,
          pageNumber: pdfPage,
          scale,
          outputScale: 1,
          backingWidth: 0,
          backingHeight: 0,
          errorTitle: diagnostic.title,
          errorMessage: `${diagnostic.message} · URL: ${resolvedPdfUrl || "n/a"}`
        });
      }
    }

    async function renderPage() {
      setRenderState((current) => ({
        ...current,
        status: "loading",
        pageNumber: pdfPage,
        scale,
        errorTitle: "",
          errorMessage: ""
      }));
      let pdfjsLib;
      try {
        pdfjsLib = await loadPdfJsForPreview();
      } catch (error) {
        fail("runtime", error);
        return;
      }
      try {
        loadingTask = pdfjsLib.getDocument({
          url: resolvedPdfUrl,
          disableRange: true,
          disableStream: true,
          withCredentials: false
        });
      } catch (error) {
        fail("worker", error);
        return;
      }

      let pdf;
      try {
        pdf = await loadingTask.promise;
      } catch (error) {
        fail("load", error);
        return;
      }

      let page;
      try {
        page = await pdf.getPage(pdfPage);
      } catch (error) {
        fail("page", error);
        return;
      }

      try {
        const baseViewport = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale });
        const outputScale = getPdfOutputScale();
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        const context = canvas.getContext("2d");
        if (!context) {
          fail("render", new Error("Canvas 2D context unavailable"));
          return;
        }
        const backingWidth = Math.floor(viewport.width * outputScale);
        const backingHeight = Math.floor(viewport.height * outputScale);
        canvas.width = backingWidth;
        canvas.height = backingHeight;
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const renderContext = {
          canvasContext: context,
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined
        };
        await page.render(renderContext).promise;
        if (!cancelled) {
          setRenderState({
            status: "ready",
            width: viewport.width,
            height: viewport.height,
            baseWidth: baseViewport.width,
            baseHeight: baseViewport.height,
            pageNumber: pdfPage,
            scale,
            outputScale,
            backingWidth,
            backingHeight,
            errorTitle: "",
            errorMessage: ""
          });
        }
      } catch (error) {
        fail("render", error);
      }
    }

    renderPage();

    return () => {
      cancelled = true;
      if (loadingTask) loadingTask.destroy();
    };
  }, [resolvedPdfUrl, pdfPage, scale]);

  useEffect(() => {
    if (renderState.status !== "ready" || !canFocusHighlight || !focusKey || !scrollerRef.current || !pageWidth || !pageHeight) return;
    if (!shouldApplyAutoFocus({
      focusKey,
      manualZoomKey: manualZoomKeyRef.current,
      completedFocusKey: autoFocusKeyRef.current
    })) return;

    const scroller = scrollerRef.current;
    const focus = focusToHighlightUnion({
      rects,
      pageWidth,
      pageHeight,
      containerWidth: scroller.clientWidth,
      containerHeight: scroller.clientHeight,
      mode: focusMode,
      minScale: FOCUS_MIN_SCALE,
      maxScale: MAX_SCALE
    });
    if (!focus) return;

    pendingFocusRef.current = { key: focusKey, focus, desiredScale: focus.desiredScale };
    if (Math.abs(focus.desiredScale - scale) > 0.05) {
      setScale(clampScale(focus.desiredScale));
    }
  }, [renderState.status, canFocusHighlight, focusKey, rects, pageWidth, pageHeight, focusMode, scale]);

  useEffect(() => {
    const pending = pendingFocusRef.current;
    if (renderState.status !== "ready" || !pending || pending.key !== focusKey || !scrollerRef.current) return;
    if (manualZoomKeyRef.current === focusKey) {
      pendingFocusRef.current = null;
      return;
    }
    if (!isRenderReadyForFocus({
      renderState,
      pageWidth,
      pageHeight,
      scale,
      desiredScale: pending.desiredScale,
      pageNumber: renderState.pageNumber,
      locationPage: pdfPage
    })) return;

    const scroller = scrollerRef.current;
    const scroll = calculateHighlightScroll({
      focus: pending.focus,
      renderedWidth: renderState.width,
      renderedHeight: renderState.height,
      pageWidth,
      pageHeight,
      containerWidth: scroller.clientWidth,
      containerHeight: scroller.clientHeight,
      scrollWidth: scroller.scrollWidth,
      scrollHeight: scroller.scrollHeight
    });
    if (!scroll) return;

    scroller.scrollLeft = scroll.left;
    scroller.scrollTop = scroll.top;
    pendingFocusRef.current = null;
    autoFocusKeyRef.current = focusKey;
  }, [renderState.status, renderState.width, renderState.height, renderState.pageNumber, renderState.scale, pageWidth, pageHeight, focusKey, scale, pdfPage]);

  function setZoom(nextScale, options = {}) {
    if (options.manual !== false && focusKey) {
      manualZoomKeyRef.current = focusKey;
      pendingFocusRef.current = null;
    }
    setScale(clampScale(nextScale));
  }

  function fitWidth() {
    const scroller = scrollerRef.current;
    const baseWidth = pageWidth || renderState.baseWidth;
    if (!scroller || !baseWidth) return;
    const availableWidth = Math.max(120, scroller.clientWidth - 24);
    setZoom(availableWidth / baseWidth);
  }

  function rectStyle(rect) {
    const safeWidth = pageWidth || 1;
    const safeHeight = pageHeight || 1;
    return {
      left: `${(rect.x0 / safeWidth) * 100}%`,
      top: `${(rect.y0 / safeHeight) * 100}%`,
      width: `${((rect.x1 - rect.x0) / safeWidth) * 100}%`,
      height: `${((rect.y1 - rect.y0) / safeHeight) * 100}%`
    };
  }

  return (
    <section className="pdfPreviewPanel" aria-label="PDF 定位预览">
      <div className="pdfPreviewHeader">
        <div>
          <h3>PDF 定位预览</h3>
          <p>
            {chunkId ? `chunk ${chunkId} · ` : ""}第 {pdfPage || "n/a"} 页 · {statusLabel} · {countLabel}
          </p>
        </div>
        <div className="pdfZoomControls" aria-label="PDF 缩放">
          <button type="button" onClick={() => setZoom(scale - SCALE_STEP)} disabled={scale <= MIN_SCALE}>-</button>
          <button type="button" onClick={() => setZoom(1)}>100%</button>
          <button type="button" onClick={() => setZoom(scale + SCALE_STEP)} disabled={scale >= MAX_SCALE}>+</button>
          <button type="button" onClick={fitWidth}>适应宽度</button>
          <span>{zoomLabel}</span>
        </div>
      </div>
      {emptyReason && (
        <div className="pdfPreviewEmpty">
          <strong>{emptyReason}</strong>
          <span>无法渲染 PDF 预览，请检查证据是否包含文档与页码信息。</span>
        </div>
      )}
      {renderState.status === "error" && (
        <div className="pdfPreviewError">
          <strong>{renderState.errorTitle || "PDF 预览暂不可用"}</strong>
          {renderState.errorMessage && <span>{renderState.errorMessage}</span>}
        </div>
      )}
      {renderState.status === "ready" && (isApproximateRegion || !rects.length) && (
        <div className={`pdfPreviewNotice${isApproximateRegion ? " approximate" : ""}`}>
          {isApproximateRegion
            ? "已显示近似页内提示，非精确文字高亮。"
            : location?.locator_reason || "页码定位成功，精确文本坐标未找到；已显示对应页面。"}
          {noticeText && (
            <span>{noticeText}</span>
          )}
        </div>
      )}
      <div className="pdfPreviewScroller" ref={scrollerRef}>
        <div className="pdfPageCanvasWrap" style={canvasSizeStyle}>
          <canvas ref={canvasRef} />
          {renderState.status === "ready" && shouldShowHighlights && (
            <div className="pdfHighlightLayer" aria-hidden="true">
              {rects.map((rect, index) => (
                <span
                  className="pdfHighlight"
                  data-mode={pdfHighlightMode(location)}
                  key={`${rect.x0}-${rect.y0}-${rect.x1}-${rect.y1}-${index}`}
                  style={rectStyle(rect)}
                />
              ))}
            </div>
          )}
          {renderState.status === "loading" && <div className="pdfPreviewLoading">正在渲染 PDF...</div>}
        </div>
      </div>
    </section>
  );
}

async function loadPdfJsForPreview() {
  if (typeof DOMMatrix === "undefined") {
    throw new Error("DOMMatrix is not available in this runtime");
  }
  if (!pdfjsLoadPromise) {
    pdfjsLoadPromise = Promise.all([
      import("pdfjs-dist"),
      import("pdfjs-dist/build/pdf.worker.mjs?url")
    ]).then(([pdfjsLib, workerModule]) => {
      pdfjsLib.GlobalWorkerOptions.workerSrc = workerModule.default;
      return pdfjsLib;
    });
  }
  return pdfjsLoadPromise;
}

function normalizePdfJsUrl(url) {
  const withoutHash = stripHash(url);
  if (!withoutHash || withoutHash.startsWith("zotero://")) return "";
  if (withoutHash.startsWith("http://127.0.0.1:8000/api/")) {
    return withoutHash.replace("http://127.0.0.1:8000", "");
  }
  if (withoutHash.startsWith("http://localhost:8000/api/")) {
    return withoutHash.replace("http://localhost:8000", "");
  }
  return withoutHash;
}

function normalizePage(value) {
  const page = Number(value);
  if (!Number.isFinite(page) || page < 1) return null;
  return Math.floor(page);
}

function clampScale(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_SCALE;
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, Number(numeric.toFixed(2))));
}

function getPdfOutputScale() {
  const ratio = typeof window !== "undefined" ? Number(window.devicePixelRatio || 1) : 1;
  if (!Number.isFinite(ratio) || ratio <= 0) return 1;
  return Math.min(2, ratio);
}

function pdfPreviewError(stage, error) {
  const rawMessage = sanitizePreviewError(error?.message || error?.name || "unknown error");
  const statusMatch = rawMessage.match(/\b(404|500)\b/);
  if (statusMatch?.[1] === "404") {
    return {
      title: "真实 PDF 暂不可用",
      message: "当前显示证据预览。",
      consoleMessage: `${stage}: PDF endpoint returned structured 404`
    };
  }
  if (statusMatch?.[1] === "500") {
    return {
      title: "PDF endpoint 错误",
      message: rawMessage,
      consoleMessage: `${stage}: ${rawMessage}`
    };
  }
  const titleByStage = {
    worker: "PDF worker 加载失败",
    load: "PDF 加载失败",
    page: "PDF 页码不可用",
    render: "PDF 页面渲染失败"
  };
  return {
    title: titleByStage[stage] || "PDF 预览暂不可用",
    message: rawMessage,
    consoleMessage: `${stage}: ${rawMessage}`
  };
}

function pdfLocationStatusLabel(location, highlightCount) {
  const status = location?.locator_status || location?.status;
  if (highlightCount > 0) {
    if (status === "layout_line_location") return "已按行定位";
    if (status === "layout_sentence_location") return "已按句定位";
    if (status === "layout_block_location" || status === "layout_bbox_location" || isLayoutLocation(location)) {
      return "已按版面块定位";
    }
    if (status === "fallback_term_found" && isLowConfidenceFallback(location)) {
      return "已显示近似页内提示，非精确文字高亮";
    }
    if (status === "fallback_term_found") return `已根据搜索词定位 ${location?.matched_term || "搜索词"}`;
    if (status === "partial_chunk_aligned") return "已找到近似高亮";
    if (status === "chunk_aligned") return "已定位到片段";
    return "已定位文本位置";
  }
  if (status === "page_level_only" || status === "not_found") {
    return "页码定位成功，精确文本坐标未找到";
  }
  if (status === "pdf_missing") return "PDF 文件不可用";
  if (status === "no_page") return "缺少页码";
  if (status === "no_text") return "缺少文本";
  if (status === "metadata_non_locatable") return "元信息不可定位";
  return location?.locator_reason || "页码定位";
}

function isLowConfidenceFallback(location) {
  return location?.confidence === "low" && location?.match_method === "fallback_chunk_text_anchor";
}

function isApproximateLocation(location) {
  return location?.visual_mode === "approximate_chunk_region"
    || (location?.confidence === "low" && location?.match_method === "fallback_chunk_text_anchor");
}

function isLayoutLocation(location) {
  return location?.visual_mode === "layout_block_highlight"
    || location?.visual_mode === "layout_line_highlight"
    || location?.locator_status === "layout_line_location"
    || location?.locator_status === "layout_sentence_location"
    || location?.locator_status === "layout_block_location"
    || location?.locator_status === "layout_bbox_location"
    || location?.is_layout_text_highlight;
}

function pdfHighlightMode(location) {
  if (isApproximateLocation(location)) return "approximate";
  if (location?.visual_mode === "layout_line_highlight") return "line";
  if (isLayoutLocation(location)) return "layout";
  return "exact";
}

function pdfHighlightCountLabel(location, count) {
  if (isApproximateLocation(location)) return `${count} 个近似区域`;
  if (location?.visual_mode === "layout_line_highlight" || location?.locator_status === "layout_line_location") return `${count} 行定位`;
  if (location?.locator_status === "layout_sentence_location") return `${count} 句定位`;
  if (isLayoutLocation(location)) return `${count} 个版面定位块`;
  return `${count} 个文本高亮`;
}

function sanitizePreviewError(message) {
  return String(message || "")
    .replace(/file:\/\/\S+/gi, "[local-file]")
    .replace(/[A-Za-z]:[\\/][^\s)"']+/g, "[local-path]")
    .slice(0, 220);
}

export default PdfLocationPreview;

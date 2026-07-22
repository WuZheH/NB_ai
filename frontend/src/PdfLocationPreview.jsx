import { useEffect, useMemo, useRef, useState } from "react";
import {
  calculateHighlightScroll,
  focusToHighlightUnion,
  isRenderReadyForFocus,
  shouldApplyAutoFocus
} from "./utils/pdfFocus.js";
import {
  isPdfPreviewSemanticallyReady,
  resolvePdfPageRequest,
  resolveScaleTransition
} from "./utils/pdfPreviewReady.js";
import { cleanSearchSnippet } from "./utils/snippet.js";

const DEFAULT_SCALE = 1.35;
const MIN_SCALE = 0.75;
const FOCUS_MIN_SCALE = 1;
const MAX_SCALE = 3.5;
const SCALE_STEP = 0.15;
const PDF_RENDER_TIMEOUT_MS = 15_000;
const PDF_RENDER_MAX_RETRIES = 1;
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
  highlightText,
  restoreState,
  fitWidthOnLoad = false
}) {
  const canvasRef = useRef(null);
  const scrollerRef = useRef(null);
  const autoFocusKeyRef = useRef("");
  const manualZoomKeyRef = useRef("");
  const pendingFocusRef = useRef(null);
  const autoFitKeyRef = useRef("");
  const renderAttemptRef = useRef(0);
  const renderRetryRef = useRef({ key: "", count: 0 });
  const focusEffectRunRef = useRef(0);
  const initialRestoreScale = readRestoreScale(restoreState, { documentId, chunkId, requestedPage: normalizePage(location?.pdf_page ?? page ?? pageNumber ?? extractPage(pdfUrl) ?? pdf_page_start ?? pdf_page_end) });
  const [scale, setScale] = useState(initialRestoreScale || DEFAULT_SCALE);
  const [autoFitState, setAutoFitState] = useState({ pdfKey: "", scale: null });
  const [completedFocusKey, setCompletedFocusKey] = useState("");
  const [completedRestoreKey, setCompletedRestoreKey] = useState("");
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [renderState, setRenderState] = useState({
    attempt: 0,
    status: "idle",
    width: 0,
    height: 0,
    baseWidth: 0,
    baseHeight: 0,
    requestedPageNumber: null,
    pageNumber: null,
    pageCount: 0,
    pageFallback: false,
    pageFallbackReason: "",
    scale: null,
    outputScale: 1,
    backingWidth: 0,
    backingHeight: 0,
    errorTitle: "",
    errorMessage: ""
  });
  const [renderRetryEpoch, setRenderRetryEpoch] = useState(0);

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

  const pageFallback = Boolean(renderState.pageFallback && renderState.requestedPageNumber === pdfPage);
  const overlayAvailable = shouldShowHighlights && !pageFallback;
  const canFocusHighlight = overlayAvailable && rects.length > 0 && !isApproximateRegion;
  const focusMode = isLayoutRegion ? "layout" : "exact";
  const pdfSelectionKey = `${resolvedPdfUrl}:${pdfPage || ""}`;
  const restoreRequest = normalizeRestoreRequest(restoreState, { documentId, chunkId, requestedPage: pdfPage });
  const restoreKey = restoreRequest
    ? `${pdfSelectionKey}:${restoreRequest.documentId}:${restoreRequest.chunkId || ""}:${restoreRequest.scale}:${restoreRequest.scrollTop}:${restoreRequest.scrollLeft}`
    : "";
  const restoreRequestSettled = !restoreRequest || completedRestoreKey === restoreKey;
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
  const renderMatchesSelection = renderState.status === "ready"
    && renderState.requestedPageNumber === pdfPage
    && Number.isInteger(renderState.pageNumber)
    && renderState.pageNumber > 0
    && Number.isFinite(renderState.scale)
    && Math.abs(renderState.scale - scale) < 0.001;
  const canvasDimensionsReady = renderState.width > 0
    && renderState.height > 0
    && renderState.backingWidth > 0
    && renderState.backingHeight > 0;
  const viewportSettled = viewportSize.width > 0 && viewportSize.height > 0;
  const manualZoomSettled = Boolean(focusKey && manualZoomKeyRef.current === focusKey);
  const autoFitSettled = !fitWidthOnLoad
    || Boolean(restoreRequest && restoreRequestSettled)
    || manualZoomSettled
    || (autoFitState.pdfKey === pdfSelectionKey
      && Number.isFinite(autoFitState.scale)
      && Math.abs(autoFitState.scale - scale) < 0.001);
  const focusSettled = !canFocusHighlight
    || Boolean(restoreRequest && restoreRequestSettled)
    || completedFocusKey === focusKey
    || manualZoomSettled;
  const overlaySettled = pageFallback || !canFocusHighlight || focusSettled;
  const previewReady = renderMatchesSelection
    && canvasDimensionsReady
    && isPdfPreviewSemanticallyReady({
      resolvedPdfUrl,
      requestedPage: pdfPage,
      renderState,
      currentScale: scale,
      autoFitSettled,
      viewportSettled,
      restoreSettled: focusSettled && restoreRequestSettled,
      overlaySettled,
    });

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return undefined;
    const measure = () => {
      const next = {
        width: Math.max(0, Number(scroller.clientWidth) || 0),
        height: Math.max(0, Number(scroller.clientHeight) || 0),
      };
      setViewportSize((current) => (
        current.width === next.width && current.height === next.height ? current : next
      ));
    };
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(scroller);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  useEffect(() => {
    if (pendingFocusRef.current && pendingFocusRef.current.key !== focusKey) {
      pendingFocusRef.current = null;
    }
    setCompletedFocusKey((current) => (current && current !== focusKey ? "" : current));
  }, [focusKey]);

  useEffect(() => {
    setCompletedRestoreKey("");
    if (!restoreRequest) return;
    autoFitKeyRef.current = "";
    autoFocusKeyRef.current = "";
    pendingFocusRef.current = null;
    setScale(restoreRequest.scale);
  }, [restoreKey]);

  useEffect(() => {
    if (!resolvedPdfUrl || !pdfPage) {
      setRenderState({ attempt: 0, status: "idle", width: 0, height: 0, baseWidth: 0, baseHeight: 0, requestedPageNumber: null, pageNumber: null, pageCount: 0, pageFallback: false, pageFallbackReason: "", scale: null, outputScale: 1, backingWidth: 0, backingHeight: 0, errorTitle: "", errorMessage: "" });
      return undefined;
    }

    let cancelled = false;
    let loadingTask;
    let renderTask;
    let renderTimeoutId;
    const attempt = renderAttemptRef.current + 1;
    renderAttemptRef.current = attempt;
    const renderKey = `${resolvedPdfUrl}:${pdfPage}:${scale}`;
    if (renderRetryRef.current.key !== renderKey) {
      renderRetryRef.current = { key: renderKey, count: 0 };
    }
    const stageContext = {
      attempt,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: pdfPage,
      scale
    };
    const reportStage = (stage, detail = {}) => emitPdfPreviewStage(stage, {
      ...stageContext,
      ...detail
    });

    function fail(stage, error) {
      const diagnostic = pdfPreviewError(stage, error);
      console.error("[PDF preview]", diagnostic.consoleMessage);
      reportStage("render_failed", { failureStage: stage });
      if (!cancelled) {
        setRenderState({
          attempt,
          status: "error",
          width: 0,
          height: 0,
          baseWidth: 0,
          baseHeight: 0,
          requestedPageNumber: pdfPage,
          pageNumber: pdfPage,
          pageCount: 0,
          pageFallback: false,
          pageFallbackReason: "",
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
      reportStage("render_requested");
      setRenderState((current) => ({
        ...current,
        attempt,
        status: "loading",
        requestedPageNumber: pdfPage,
        pageNumber: pdfPage,
        pageCount: 0,
        pageFallback: false,
        pageFallbackReason: "",
        scale,
        errorTitle: "",
        errorMessage: ""
      }));
      let pdfjsLib;
      try {
        pdfjsLib = await loadPdfJsForPreview();
        reportStage("pdfjs_worker_ready");
      } catch (error) {
        fail("runtime", error);
        return;
      }
      try {
        reportStage("pdf_document_requested");
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
        reportStage("pdf_document_loaded", { pageCount: Number(pdf.numPages) || null });
      } catch (error) {
        fail("load", error);
        return;
      }

      const pageRequest = resolvePdfPageRequest(pdfPage, Number(pdf.numPages));
      if (!pageRequest.pageNumber) {
        fail("page", new Error(pageRequest.fallbackReason));
        return;
      }
      if (pageRequest.fallback) {
        reportStage("pdf_page_fallback", {
          requestedPageNumber: pdfPage,
          pageNumber: pageRequest.pageNumber,
          fallbackReason: pageRequest.fallbackReason,
        });
      }

      let page;
      try {
        page = await pdf.getPage(pageRequest.pageNumber);
        reportStage("pdf_page_loaded", { pageNumber: pageRequest.pageNumber });
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
        reportStage("canvas_dimensions_committed", {
          cssWidth: viewport.width,
          cssHeight: viewport.height,
          backingWidth,
          backingHeight
        });
        const renderContext = {
          canvasContext: context,
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined
        };
        renderTask = page.render(renderContext);
        await Promise.race([
          renderTask.promise,
          new Promise((_, reject) => {
            renderTimeoutId = window.setTimeout(() => {
              const timeoutError = new Error("PDF render timed out");
              timeoutError.code = "pdf_render_timeout";
              reject(timeoutError);
            }, PDF_RENDER_TIMEOUT_MS);
          })
        ]);
        if (renderTimeoutId) window.clearTimeout(renderTimeoutId);
        reportStage("pdf_page_rendered");
        if (!cancelled) {
          renderRetryRef.current = { key: renderKey, count: 0 };
          reportStage("react_ready_scheduled");
          setRenderState({
            attempt,
            status: "ready",
            width: viewport.width,
            height: viewport.height,
            baseWidth: baseViewport.width,
            baseHeight: baseViewport.height,
            requestedPageNumber: pdfPage,
            pageNumber: pageRequest.pageNumber,
            pageCount: Number(pdf.numPages),
            pageFallback: pageRequest.fallback,
            pageFallbackReason: pageRequest.fallbackReason,
            scale,
            outputScale,
            backingWidth,
            backingHeight,
            errorTitle: "",
            errorMessage: ""
          });
        }
      } catch (error) {
        if (renderTimeoutId) window.clearTimeout(renderTimeoutId);
        const retryState = renderRetryRef.current;
        if (
          error?.code === "pdf_render_timeout"
          && !cancelled
          && retryState.key === renderKey
          && retryState.count < PDF_RENDER_MAX_RETRIES
        ) {
          retryState.count += 1;
          reportStage("render_retry_scheduled", { retryCount: retryState.count });
          try { renderTask?.cancel(); } catch {}
          setRenderRetryEpoch((current) => current + 1);
          return;
        }
        fail("render", error);
      }
    }

    renderPage();

    return () => {
      cancelled = true;
      if (renderTimeoutId) window.clearTimeout(renderTimeoutId);
      reportStage("render_cancelled");
      try { renderTask?.cancel(); } catch {}
      if (loadingTask) loadingTask.destroy();
    };
  }, [resolvedPdfUrl, pdfPage, scale, renderRetryEpoch]);

  useEffect(() => {
    if (renderState.status !== "ready") return;
    emitPdfPreviewStage("react_ready_committed", {
      attempt: renderState.attempt,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: renderState.pageNumber,
      scale: renderState.scale,
      cssWidth: renderState.width,
      cssHeight: renderState.height,
      backingWidth: renderState.backingWidth,
      backingHeight: renderState.backingHeight
    });
  }, [renderState, documentId, chunkId]);

  useEffect(() => {
    if (!previewReady) return;
    emitPdfPreviewStage("preview_ready_committed", {
      attempt: renderState.attempt,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: renderState.pageNumber,
      scale: renderState.scale,
      cssWidth: renderState.width,
      cssHeight: renderState.height,
      backingWidth: renderState.backingWidth,
      backingHeight: renderState.backingHeight,
      highlightStrategy: pdfHighlightMode(location),
      highlightCount: rects.length
    });
  }, [
    previewReady,
    renderState.attempt,
    renderState.pageNumber,
    renderState.scale,
    renderState.width,
    renderState.height,
    renderState.backingWidth,
    renderState.backingHeight,
    documentId,
    chunkId,
    location,
    rects.length
  ]);

  useEffect(() => {
    if (!restoreRequest || completedRestoreKey === restoreKey || renderState.status !== "ready" || !scrollerRef.current) return;
    if (renderState.requestedPageNumber !== pdfPage || Math.abs(Number(renderState.scale) - restoreRequest.scale) > 0.001) return;
    const scroller = scrollerRef.current;
    scroller.scrollTop = restoreRequest.scrollTop;
    scroller.scrollLeft = restoreRequest.scrollLeft;
    pendingFocusRef.current = null;
    autoFocusKeyRef.current = focusKey;
    if (focusKey) setCompletedFocusKey(focusKey);
    setCompletedRestoreKey(restoreKey);
    emitPdfPreviewStage("preview_restore_committed", {
      attempt: renderState.attempt,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: renderState.pageNumber,
      scale: renderState.scale,
      scrollTop: scroller.scrollTop,
      scrollLeft: scroller.scrollLeft,
    });
  }, [restoreKey, completedRestoreKey, renderState.status, renderState.requestedPageNumber, renderState.pageNumber, renderState.scale, renderState.attempt, pdfPage, focusKey, documentId, chunkId]);

  useEffect(() => {
    if (restoreRequest || !fitWidthOnLoad || renderState.status !== "ready" || !scrollerRef.current || !viewportSettled) return;
    const baseWidth = pageWidth || renderState.baseWidth;
    if (!baseWidth) return;
    const fitKey = `${resolvedPdfUrl}:${pdfPage}:${Math.round(viewportSize.width)}`;
    if (autoFitKeyRef.current === fitKey) return;
    autoFitKeyRef.current = fitKey;
    const fitScale = clampScale(Math.max(120, viewportSize.width - 24) / baseWidth);
    const transition = resolveScaleTransition({ currentScale: scale, targetScale: fitScale });
    setAutoFitState({ pdfKey: pdfSelectionKey, scale: transition.settledScale });
    if (transition.shouldUpdate) setZoom(transition.settledScale, { manual: false });
  }, [restoreKey, fitWidthOnLoad, renderState.status, renderState.baseWidth, pageWidth, resolvedPdfUrl, pdfPage, pdfSelectionKey, scale, viewportSettled, viewportSize.width]);

  useEffect(() => {
    focusEffectRunRef.current += 1;
    const effectRun = focusEffectRunRef.current;
    const scroller = scrollerRef.current;
    const stageDetail = {
      attempt: renderState.attempt,
      effectRun,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: renderState.pageNumber,
      focusKey,
      pendingFocusKey: pendingFocusRef.current?.key || "",
      clientWidth: viewportSize.width,
      clientHeight: viewportSize.height,
      scrollWidth: Number(scroller?.scrollWidth || 0),
      scrollHeight: Number(scroller?.scrollHeight || 0),
      pageWidth: Number(pageWidth || 0),
      pageHeight: Number(pageHeight || 0),
      renderedWidth: Number(renderState.width || 0),
      renderedHeight: Number(renderState.height || 0),
      currentScale: Number(scale || 0),
    };
    if (restoreRequest || renderState.status !== "ready" || !canFocusHighlight || !focusKey || !pageWidth || !pageHeight) return;
    if (!scroller || !viewportSettled) {
      emitPdfPreviewStage("preview_focus_waiting", { ...stageDetail, reason: "viewport_not_ready" });
      return;
    }
    if (!shouldApplyAutoFocus({
      focusKey,
      manualZoomKey: manualZoomKeyRef.current,
      completedFocusKey: autoFocusKeyRef.current
    })) return;

    const focus = focusToHighlightUnion({
      rects,
      pageWidth,
      pageHeight,
      containerWidth: viewportSize.width,
      containerHeight: viewportSize.height,
      mode: focusMode,
      minScale: FOCUS_MIN_SCALE,
      maxScale: MAX_SCALE
    });
    if (!focus) {
      emitPdfPreviewStage("preview_focus_waiting", { ...stageDetail, reason: "focus_geometry_invalid" });
      return;
    }

    const fitScale = fitWidthOnLoad && viewportSize.width && pageWidth
      ? clampScale(Math.max(120, viewportSize.width - 24) / pageWidth)
      : focus.desiredScale;
    const desiredScale = fitWidthOnLoad ? Math.min(focus.desiredScale, fitScale) : focus.desiredScale;
    const transition = resolveScaleTransition({ currentScale: scale, targetScale: clampScale(desiredScale) });
    pendingFocusRef.current = { key: focusKey, focus, desiredScale: transition.settledScale };
    emitPdfPreviewStage("preview_focus_pending", {
      ...stageDetail,
      desiredScale: transition.settledScale,
      focus,
    });
    if (transition.shouldUpdate) {
      setScale(transition.settledScale);
    }
  }, [restoreKey, renderState.status, renderState.attempt, renderState.pageNumber, renderState.width, renderState.height, canFocusHighlight, focusKey, rects, pageWidth, pageHeight, focusMode, scale, fitWidthOnLoad, viewportSettled, viewportSize.width, viewportSize.height, documentId, chunkId]);

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
    setCompletedFocusKey(focusKey);
    emitPdfPreviewStage("preview_focus_committed", {
      attempt: renderState.attempt,
      effectRun: focusEffectRunRef.current,
      documentId: Number(documentId) || null,
      chunkId: Number(chunkId) || null,
      pageNumber: renderState.pageNumber,
      scale: renderState.scale,
      focusKey,
      desiredScale: pending.desiredScale,
      clientWidth: viewportSize.width,
      clientHeight: viewportSize.height,
      scrollWidth: scroller.scrollWidth,
      scrollHeight: scroller.scrollHeight,
      pageWidth,
      pageHeight,
      renderedWidth: renderState.width,
      renderedHeight: renderState.height,
      left: scroll.left,
      top: scroll.top,
    });
  }, [renderState.status, renderState.width, renderState.height, renderState.pageNumber, renderState.scale, renderState.attempt, pageWidth, pageHeight, focusKey, scale, pdfPage, viewportSize.width, viewportSize.height, documentId, chunkId]);

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
    const top = location?.coordinate_origin === "pdf_bottom_left"
      ? safeHeight - rect.y1
      : rect.y0;
    return {
      left: `${(rect.x0 / safeWidth) * 100}%`,
      top: `${(top / safeHeight) * 100}%`,
      width: `${((rect.x1 - rect.x0) / safeWidth) * 100}%`,
      height: `${((rect.y1 - rect.y0) / safeHeight) * 100}%`
    };
  }

  return (
    <section
      className="pdfPreviewPanel"
      aria-label="PDF 定位预览"
      data-testid="pdf-location-preview"
      data-preview-status={renderState.status}
      data-preview-ready={previewReady ? "true" : "false"}
      data-render-attempt={renderState.attempt}
      data-document-id={documentId || ""}
      data-page-number={renderState.pageNumber || ""}
      data-requested-page-number={renderState.requestedPageNumber || ""}
      data-page-fallback={pageFallback ? "true" : "false"}
      data-preview-restore-status={previewReady ? "restored" : renderState.status}
      data-chunk-id={chunkId || ""}
      data-render-scale={renderState.scale || ""}
      data-canvas-width={renderState.width || 0}
      data-canvas-height={renderState.height || 0}
      data-canvas-backing-width={renderState.backingWidth || 0}
      data-canvas-backing-height={renderState.backingHeight || 0}
      data-viewport-width={viewportSize.width}
      data-viewport-height={viewportSize.height}
      data-highlight-strategy={previewReady && overlayAvailable ? pdfHighlightMode(location) : ""}
      data-highlight-count={previewReady && overlayAvailable ? rects.length : 0}
    >
      <div className="pdfPreviewHeader">
        <div>
          <h3>PDF 定位预览</h3>
          <p>
            {chunkId ? `chunk ${chunkId} · ` : ""}第 {renderState.pageNumber || pdfPage || "n/a"} 页 · {statusLabel} · {countLabel}
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
      {renderState.status === "ready" && pageFallback && (
        <div className="pdfPreviewNotice approximate" data-testid="pdf-page-fallback-notice">
          请求页码超出文档范围，已显示最后一页；当前页不显示原页高光。
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
        <div className="pdfPageCanvasWrap" style={canvasSizeStyle} data-testid="pdf-page-wrap">
          <canvas ref={canvasRef} data-testid="pdf-page-canvas" />
          {renderState.status === "ready" && overlayAvailable && (
            <div className="pdfHighlightLayer" aria-hidden="true" data-testid="pdf-highlight-layer" data-strategy={pdfHighlightMode(location)}>
              {rects.map((rect, index) => (
                <span
                  className="pdfHighlight"
                  data-testid="pdf-highlight-rect"
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
      import("pdfjs-dist/legacy/build/pdf.mjs"),
      import("pdfjs-dist/legacy/build/pdf.worker.mjs?url")
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

function readRestoreScale(restoreState, identity) {
  return normalizeRestoreRequest(restoreState, identity)?.scale || null;
}

function normalizeRestoreRequest(restoreState, { documentId, chunkId, requestedPage } = {}) {
  if (!restoreState || typeof restoreState !== "object") return null;
  const expectedDocumentId = Number(documentId);
  const expectedChunkId = Number(chunkId);
  const expectedPage = Number(requestedPage);
  const value = {
    documentId: Number(restoreState.document_id),
    chunkId: Number(restoreState.chunk_id) || null,
    requestedPage: Number(restoreState.requested_page_number),
    scale: Number(restoreState.scale),
    scrollTop: Number(restoreState.scroll_top),
    scrollLeft: Number(restoreState.scroll_left),
  };
  if (
    !Number.isInteger(value.documentId)
    || value.documentId !== expectedDocumentId
    || (Number.isInteger(expectedChunkId) && expectedChunkId > 0 && value.chunkId !== expectedChunkId)
    || !Number.isInteger(value.requestedPage)
    || value.requestedPage !== expectedPage
    || !Number.isFinite(value.scale)
    || value.scale < MIN_SCALE
    || value.scale > MAX_SCALE
    || !Number.isFinite(value.scrollTop)
    || value.scrollTop < 0
    || !Number.isFinite(value.scrollLeft)
    || value.scrollLeft < 0
  ) return null;
  return value;
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

function emitPdfPreviewStage(stage, detail = {}) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(new CustomEvent("search:pdf-preview-stage", {
    detail: { stage, ...detail }
  }));
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

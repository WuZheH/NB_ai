import { useEffect, useRef, useState } from "react";

const COVER_RENDER_SCALE = 0.55;
let pdfjsLoadPromise;

function PdfCoverThumbnail({ apiBase, documentId, title, documentType }) {
  const canvasRef = useRef(null);
  const loadingTaskRef = useRef(null);
  const cancelledRef = useRef(false);
  const renderIdRef = useRef(0);
  const [state, setState] = useState("idle");
  const [errorStage, setErrorStage] = useState(null);
  const [pageRatio, setPageRatio] = useState(null);
  const coverKind = documentCoverKind(documentType);

  useEffect(() => {
    if (!apiBase || documentId == null) {
      setState("fallback");
      setErrorStage("no_params");
      return undefined;
    }

    // Reset state for new documentId
    cancelledRef.current = false;
    setState("loading");
    setErrorStage(null);
    setPageRatio(null);
    const renderId = ++renderIdRef.current;

    // Clear previous canvas content
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      canvas.width = 0;
      canvas.height = 0;
    }

    async function renderCover() {
      const currentRenderId = renderId;
      // Cache-bust: add documentId to prevent browser/pdfjs caching wrong PDF
      const pdfUrl = `${apiBase}/api/v1/library/documents/${documentId}/pdf?v=${documentId}`;
      const coverLog = (stage, extra = {}) =>
        console.debug("[PDF cover]", { documentId, stage, renderId: currentRenderId, ...extra });

      function isCancelled() {
        return cancelledRef.current || renderIdRef.current !== currentRenderId;
      }

      async function renderPageOne(pdf) {
        if (isCancelled()) return;
        coverLog("getPage-start");
        let page;
        try {
          page = await pdf.getPage(1);
        } catch (e) {
          if (isCancelled()) return;
          console.warn("[PDF cover] getPage failed", { documentId, error: sanitizeCoverError(e?.message) });
          setErrorStage("render_error_getPage");
          setState("fallback");
          return;
        }
        if (isCancelled()) return;
        coverLog("getPage-ok");

        const viewport = page.getViewport({ scale: COVER_RENDER_SCALE });
        setPageRatio(viewport.width / viewport.height);
        const c = canvasRef.current;
        if (!c) {
          if (isCancelled()) return;
          setErrorStage("render_error_no_canvas");
          setState("fallback");
          return;
        }
        if (isCancelled()) return;

        const context = c.getContext("2d");
        if (!context) {
          if (isCancelled()) return;
          setErrorStage("render_error_no_context");
          setState("fallback");
          return;
        }
        c.width = Math.floor(viewport.width);
        c.height = Math.floor(viewport.height);
        coverLog("render-start");

        try {
          await page.render({ canvasContext: context, viewport }).promise;
        } catch (e) {
          if (isCancelled()) return;
          console.warn("[PDF cover] page.render failed", { documentId, error: sanitizeCoverError(e?.message) });
          setErrorStage("render_error_pageRender");
          setState("fallback");
          return;
        }
        if (isCancelled()) return;
        coverLog("render-ok");
        setErrorStage(null);
        setState("ready");
      }

      let pdfjsLib;
      try {
        pdfjsLib = await loadPdfJsForCover();
      } catch (error) {
        if (isCancelled()) return;
        console.warn("[PDF cover] pdfjs unavailable", { documentId, error: sanitizeCoverError(error?.message) });
        setErrorStage("pdfjs_unavailable");
        setState("fallback");
        return;
      }

      // ---- Path 1: URL getDocument ----
      coverLog("url-getDocument-start");
      try {
        const task = pdfjsLib.getDocument({
          url: pdfUrl,
          disableRange: true,
          disableStream: true,
          disableAutoFetch: true,
          withCredentials: false,
        });
        loadingTaskRef.current = task;
        const pdf = await task.promise;
        if (isCancelled()) return;
        coverLog("url-getDocument-ok");
        await renderPageOne(pdf);
        return;
      } catch (error) {
        if (isCancelled()) return;
        const reason = sanitizeCoverError(error?.message || error?.name || "URL loading failed");
        console.warn("[PDF cover] url path failed, falling back to data path", {
          documentId, stage: "url-getDocument-fail", reason,
        });
        if (loadingTaskRef.current) {
          try { await loadingTaskRef.current.destroy(); } catch { /* ignore */ }
          loadingTaskRef.current = null;
        }
      }

      // ---- Path 2: fetch + data getDocument ----
      coverLog("fetch-start");
      try {
        const response = await fetch(pdfUrl);
        if (isCancelled()) return;
        const pdfSize = response.headers.get("content-length") || undefined;
        coverLog("fetch-response", { status: response.status, pdfSize });
        if (!response.ok) {
          if (isCancelled()) return;
          console.warn("[PDF cover] fetch failed", { documentId, status: response.status });
          setErrorStage(`fetch_error_${response.status}`);
          setState("fallback");
          return;
        }

        coverLog("fetch-arraybuffer");
        const arrayBuffer = await response.arrayBuffer();
        if (isCancelled()) return;

        coverLog("data-getDocument-start", { pdfSize });
        // Must use Uint8Array — pdfjs needs typed array, not plain ArrayBuffer
        const task = pdfjsLib.getDocument({
          data: new Uint8Array(arrayBuffer),
          disableRange: true,
          disableStream: true,
        });
        loadingTaskRef.current = task;
        const pdf = await task.promise;
        if (isCancelled()) return;
        coverLog("data-getDocument-ok");
        await renderPageOne(pdf);
      } catch (error) {
        if (isCancelled()) return;
        const reason = sanitizeCoverError(error?.message || error?.name || "Data loading failed");
        console.warn("[PDF cover] data path failed", { documentId, reason });
        setErrorStage("data_path_fail");
        setState("fallback");
      }
    }

    renderCover();

    return () => {
      cancelledRef.current = true;
      if (loadingTaskRef.current) {
        try { loadingTaskRef.current.destroy(); } catch { /* ignore */ }
        loadingTaskRef.current = null;
      }
      // Clear canvas on unmount/change
      const c = canvasRef.current;
      if (c) {
        const ctx = c.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, c.width, c.height);
        c.width = 0;
        c.height = 0;
      }
    };
  }, [apiBase, documentId]);

  return (
    <div
      className={`documentCover pdfCoverState-${state}`}
      aria-label="PDF 第一页封面预览"
      data-failure-stage={errorStage || undefined}
      data-document-id={documentId}
      data-cover-kind={coverKind}
      style={pageRatio ? { "--pdf-cover-ratio": pageRatio } : undefined}
    >
      {state === "loading" && <div className="pdfCoverSkeleton" />}
      <canvas
        ref={canvasRef}
        aria-hidden={state !== "ready"}
        style={{ position: "absolute", top: 0, left: 0 }}
      />
      {state !== "ready" && state !== "loading" && (
        <div className="pdfCoverFallback" aria-hidden="true">
          <span>{documentCoverKindLabel(coverKind)}</span>
          <strong>{documentCoverTitle(title)}</strong>
        </div>
      )}
    </div>
  );
}

function documentCoverKind(documentType = "") {
  const kind = String(documentType || "").toLowerCase();
  if (kind === "book") return "book";
  if (kind === "paper" || kind === "thesis" || kind === "report") return "paper";
  return "pdf";
}

function documentCoverKindLabel(kind) {
  return {
    book: "书籍",
    paper: "论文",
    pdf: "PDF",
  }[kind] || "PDF";
}

async function loadPdfJsForCover() {
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

function documentCoverTitle(title = "") {
  const words = String(title)
    .replace(/[^\w\s-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return "PDF";
  return words
    .slice(0, 5)
    .map((word) => word.slice(0, 12))
    .join(" ");
}

function sanitizeCoverError(message) {
  return String(message || "")
    .replace(/file:\/\/\S+/gi, "[local-file]")
    .replace(/[A-Za-z]:[\\/][^\s)"']+/g, "[local-path]")
    .slice(0, 180);
}

export default PdfCoverThumbnail;

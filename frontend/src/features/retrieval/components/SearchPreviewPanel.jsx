import { useEffect, useState } from "react";
import FragmentIdBlock from "../../../shared/components/FragmentIdBlock.jsx";
import SourceBadge from "../../../shared/components/SourceBadge.jsx";
import PdfLocationPreview from "../../../PdfLocationPreview.jsx";
import { buildSearchPdfLocationPreview } from "../adapters/searchPdfLocationPreview.js";
import { pageLabel } from "../utils/notebookSearch.js";

export default function SearchPreviewPanel({ state, onClose, onCopyFragment, onCopiedId, onViewChange }) {
  const result = state?.data;
  const isPdf = result?.source_type === "pdf_chunk";
  const locator = result?.locator;
  const pdfPreview = buildSearchPdfLocationPreview(result);
  const [view, setView] = useState("text");

  useEffect(() => {
    setView(result?.preview_view === "text" || result?.preview_view === "pdf"
      ? result.preview_view
      : pdfPreview.available ? "pdf" : "text");
  }, [result?.fragment_id, result?.preview_view, pdfPreview.available]);

  function selectView(nextView) {
    setView(nextView);
    onViewChange?.(nextView);
  }

  return (
    <aside className="search-preview-panel searchPreviewPanel" aria-label="片段预览" data-testid="search-preview-panel">
      <header>
        <div>
          <h2>{result?.document_title || "片段预览"}</h2>
          {result && (
            <div className="searchPreviewHeaderMeta">
              <span className="searchPreviewMetaLabel">来源摘要</span>
              <SourceBadge sourceType={result.source_type} />
              <span>{pageLabel(result)}</span>
            </div>
          )}
        </div>
        {state?.status !== "idle" && (
          <button type="button" className="search-icon-button search-button-transparent" aria-label="关闭预览" title="关闭预览" onClick={onClose}>×</button>
        )}
      </header>

      {state?.status === "idle" && (
        <div className="searchPreviewEmpty search-state-empty">
          <strong>选择一个结果进行预览</strong>
          <p>完整片段、上下文和必要来源信息会显示在这里，搜索列表不会跳转或丢失。</p>
        </div>
      )}
      {(state?.status === "loading" || state?.status === "loading_fragment") && (
        <div className="searchPreviewEmpty search-state-loading" role="status">正在读取完整 fragment…</div>
      )}
      {state?.status === "error" && (
        <div className="searchPreviewEmpty search-state-error" role="alert">{state.error}</div>
      )}
      {state?.status === "ready" && result && (
        <>
          <div className="searchPreviewTabs" role="tablist" aria-label="预览视图">
            {pdfPreview.available && (
              <button type="button" role="tab" aria-selected={view === "pdf"} className={`searchPreviewTab${view === "pdf" ? " isActive" : ""}`} onClick={() => selectView("pdf")}>PDF</button>
            )}
            <button type="button" role="tab" aria-selected={view === "text"} className={`searchPreviewTab${view === "text" ? " isActive" : ""}`} onClick={() => selectView("text")}>文本</button>
          </div>
        <div
          className={`searchPreviewContent search-scroll-region ${view === "pdf" ? "isPdfView" : "isTextView"}`}
          data-testid="search-preview-scroll"
          tabIndex={0}
          role="region"
          aria-label="可滚动的片段预览内容"
        >
          {view === "pdf" && pdfPreview.available ? (
            <div className="searchPreviewPdfStage">
              <PdfLocationPreview {...pdfPreview.props} />
            </div>
          ) : (
            <TextPreview result={result} isPdf={isPdf} />
          )}

          {view === "pdf" && (
            <section className="searchPreviewHitText">
              <h3>命中原文</h3>
              <p>{result.selected_source_text || result.coherent_text || "该 fragment 没有可显示的命中原文。"}</p>
            </section>
          )}

          <div className="searchPreviewFooter">
            <button
              type="button"
              className="search-button search-button-transparent search-button-compact searchPreviewCopy"
              onClick={() => onCopyFragment(result)}
            >
              复制片段
            </button>
            <details className="searchTechnicalDetails searchPreviewTechnicalDetails">
              <summary>技术详情</summary>
              <dl>
                <MetaRow label="document_id" value={result.document_id} />
                <MetaRow label="selection_rank" value={result.selection_rank} />
              </dl>
              <FragmentIdBlock fragmentId={result.fragment_id} onCopied={onCopiedId} />
            </details>
          </div>
        </div>
        </>
      )}
    </aside>
  );
}

function TextPreview({ result, isPdf }) {
  return (
    <>
      {isPdf ? (
        <PreviewText title="PDF 原文" value={result.coherent_text} empty="该 fragment 没有可显示的 PDF 文本。" />
      ) : (
        <>
          <PreviewText title="用户笔记" value={result.user_note} empty="该 fragment 没有用户笔记正文。" />
          <PreviewText title="对应选中文本" value={result.selected_source_text} empty="该笔记没有关联的选中文本。" />
        </>
      )}
      <PreviewText title="前文" value={result.context_before} />
      <PreviewText title="后文" value={result.context_after} />
    </>
  );
}

function PreviewText({ title, value, empty = "" }) {
  if (!value && !empty) return null;
  return (
    <section className="searchPreviewText">
      <h3>{title}</h3>
      <p>{value || empty}</p>
    </section>
  );
}

function MetaRow({ label, value }) {
  if (value === null || value === undefined || value === "") return null;
  return <div><dt>{label}</dt><dd className="search-mono">{String(value)}</dd></div>;
}

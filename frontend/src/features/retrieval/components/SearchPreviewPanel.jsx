import { useEffect, useState } from "react";
import FragmentIdBlock from "../../../shared/components/FragmentIdBlock.jsx";
import SourceBadge from "../../../shared/components/SourceBadge.jsx";
import { formatScore, pageLabel } from "../utils/notebookSearch.js";
import PdfFragmentPreview from "./PdfFragmentPreview.jsx";

export default function SearchPreviewPanel({ state, onClose, onCopyFragment, onCopiedId }) {
  const result = state?.data;
  const isPdf = result?.source_type === "pdf_chunk";
  const locator = result?.locator;
  const [view, setView] = useState("text");

  useEffect(() => {
    setView(locator?.pdf_available ? "pdf" : "text");
  }, [result?.fragment_id, locator?.pdf_available]);

  function showTextAfterPdfFailure() {
    setView("text");
  }

  return (
    <aside className="search-preview-panel searchPreviewPanel" aria-label="片段预览" data-testid="search-preview-panel">
      <header>
        <div>
          <span className="searchPreviewEyebrow">预览</span>
          <h2>{result?.document_title || "片段预览"}</h2>
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
            {locator?.pdf_available && (
              <button type="button" role="tab" aria-selected={view === "pdf"} className={`searchPreviewTab${view === "pdf" ? " isActive" : ""}`} onClick={() => setView("pdf")}>PDF</button>
            )}
            <button type="button" role="tab" aria-selected={view === "text"} className={`searchPreviewTab${view === "text" ? " isActive" : ""}`} onClick={() => setView("text")}>文本</button>
          </div>
        <div
          className="searchPreviewContent search-scroll-region"
          data-testid="search-preview-scroll"
          tabIndex={0}
          role="region"
          aria-label="可滚动的片段预览内容"
        >
          <div className="searchPreviewMeta">
            <SourceBadge sourceType={result.source_type} />
            <span>{pageLabel(result)}</span>
            {result.final_rank && <span>最终排名 #{result.final_rank}</span>}
            {result.reranker_score !== null && result.reranker_score !== undefined && (
              <span className="search-mono">reranker {formatScore(result.reranker_score)}</span>
            )}
          </div>

          {view === "pdf" && locator?.pdf_available ? (
            <PdfFragmentPreview fragment={result} locator={locator} onUnavailable={showTextAfterPdfFailure} />
          ) : (
            <TextPreview result={result} isPdf={isPdf} />
          )}

          <section className="searchPreviewProvenance">
            <h3>来源摘要</h3>
            <dl>
              <MetaRow label="document_id" value={result.document_id} />
              <MetaRow label="chunk_id" value={result.chunk_id} />
              <MetaRow label="page_label" value={result.page_label} />
              <MetaRow label="content_hash" value={result.content_hash} />
            </dl>
          </section>

          <FragmentIdBlock fragmentId={result.fragment_id} onCopied={onCopiedId} />
          <button
            type="button"
            className="search-button search-button-transparent search-button-compact searchPreviewCopy"
            onClick={() => onCopyFragment(result)}
          >
            复制片段
          </button>
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
        <PreviewText title="PDF 原文" value={result.text} empty="该 fragment 没有可显示的 PDF 文本。" />
      ) : (
        <>
          <PreviewText title="用户笔记" value={result.note_text} empty="该 fragment 没有用户笔记正文。" />
          <PreviewText title="对应选中文本" value={result.selected_text} empty="该笔记没有关联的选中文本。" />
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

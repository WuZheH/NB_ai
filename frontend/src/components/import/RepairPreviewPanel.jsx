import { useEffect, useMemo, useState } from "react";
import PdfLocationPreview from "../../PdfLocationPreview.jsx";
import RepairPlanDraftPanel from "./RepairPlanDraftPanel.jsx";

export default function RepairPreviewPanel({
  result,
  pdfPreviewUrl,
  loading,
  error,
  planResult,
  planLoading,
  planError,
  onBuildPlan,
  onBackToNormal,
  onCancelReplace,
  normalImportRecommended = false,
}) {
  const candidates = useMemo(
    () => (result?.pages || []).flatMap(page => {
      const lineById = new Map((page.ocr_lines || []).map(line => [line.line_id, line.text]));
      return (page.candidates || []).map(candidate => ({
        ...candidate,
        physical_page: page.physical_page,
        page_label: page.page_label,
        source_line_texts: (candidate.source_line_ids || []).map(lineId => lineById.get(lineId)).filter(Boolean),
        key: `${page.physical_page}:${candidate.candidate_index}`,
      }));
    }),
    [result],
  );
  const [selectedKey, setSelectedKey] = useState("");
  const [leftPanelMode, setLeftPanelMode] = useState("list");
  const [detailTextMode, setDetailTextMode] = useState("markdown");

  useEffect(() => {
    if (!candidates.some(candidate => candidate.key === selectedKey)) {
      setSelectedKey("");
      setLeftPanelMode("list");
    }
  }, [candidates, selectedKey]);

  if (loading) {
    return (
      <section className="repairPreviewPanel repairPreviewLoading" aria-label="OCR repair preview loading">
        <strong>正在生成修复预览，不会写入正式库</strong>
        <span>仅对导入预检选中的 sample pages 执行 OCR；不会启动整章或全书导入。</span>
      </section>
    );
  }
  if (error) {
    return (
      <section className="repairPreviewPanel repairPreviewLoading" aria-label="OCR repair preview error">
        <strong>OCR 修复预览暂不可用</strong>
        <span>{error}</span>
      </section>
    );
  }
  if (!result) return null;

  const activeCandidate = candidates.find(candidate => candidate.key === selectedKey);
  const diagnosticOnly = normalImportRecommended || hasCleanPdfRepairWarning(result);
  const focusedCandidateMode = leftPanelMode === "detail" && Boolean(activeCandidate);
  const pdfViews = buildRepairPdfViews(result.pages || [], activeCandidate, focusedCandidateMode);

  return (
    <section className="repairPreviewPanel" aria-label="OCR repair preview">
      <header className="repairPreviewHeader">
        <div>
          <p className="previewGateEyebrow">OCR REPAIR PREVIEW · SAMPLE ONLY</p>
          <h3>OCR repair preview</h3>
          {diagnosticOnly ? (
            <div className="repairDiagnosticNotice">
              <strong>OCR repair preview is diagnostic only for this PDF.</strong>
              <span>Normal text-layer import is recommended.</span>
            </div>
          ) : (
            <p className="repairPreviewRecommendation">{recommendationLabel(result.overall_recommendation)}</p>
          )}
        </div>
        <div className="repairPreviewBadges">
          <span>Sampled pages: {(result.ocr_scope?.pages || []).join(", ")}</span>
          <strong>No database writes</strong>
          <span>Full-book OCR: off</span>
        </div>
      </header>

      <div className="repairPreviewColumns">
        <section className="repairCandidatePanel" aria-label="OCR correction candidates">
          {leftPanelMode === "detail" && activeCandidate ? (
            <CandidateDetail
              candidate={activeCandidate}
              textMode={detailTextMode}
              onTextModeChange={setDetailTextMode}
              onBack={() => setLeftPanelMode("list")}
            />
          ) : (
            <>
              <div className="previewGatePanelHeader">
                <h4>Candidate / correction preview</h4>
                <span className="previewGateCoordinateNote">{candidates.length} candidates</span>
              </div>
              <div className="repairCandidateList">
                {candidates.map(candidate => (
                  <button
                    className={`repairCandidateCard ${candidate.key === selectedKey ? "active" : ""}`}
                    key={candidate.key}
                    type="button"
                    aria-selected={candidate.key === selectedKey}
                    onClick={() => {
                      setSelectedKey(candidate.key);
                      setDetailTextMode("markdown");
                      setLeftPanelMode("detail");
                    }}
                  >
                    <div className="repairCandidateHeading">
                      <span>Candidate {candidate.candidate_index} · Physical p.{candidate.physical_page} · label {candidate.page_label || "n/a"}</span>
                      <strong className={`repairStatus ${candidate.quality_status}`}>{candidate.quality_status}</strong>
                    </div>
                    <div className="repairCandidateSummary">
                      <span>{(candidate.source_line_ids || []).length} source lines</span>
                      <p>{candidateSnippet(candidateRepairText(candidate))}</p>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </section>

        <section className={`repairPdfPanel ${focusedCandidateMode ? "focusedCandidate" : ""}`} aria-label="PDF OCR line bbox preview">
          <div className="previewGatePanelHeader">
            <h4>{focusedCandidateMode ? "PDF original page + selected candidate lines" : "PDF original page + OCR line bbox"}</h4>
            <span className="previewGateCoordinateNote">
              {focusedCandidateMode && pdfViews[0]
                ? `physical p.${pdfViews[0].page.physical_page} / label ${pdfViews[0].page.page_label || "n/a"} · ${pdfViews[0].rects.length} source lines highlighted`
                : "physical page 定位 · label 仅展示"}
            </span>
          </div>
          {focusedCandidateMode && pdfViews[0]?.spansMultiplePages && (
            <p className="repairPdfFocusNotice">Candidate spans multiple pages; showing primary page.</p>
          )}
          {pdfViews.map(view => (
              <div className="repairRenderedPage" key={`${view.page.physical_page}:${view.focusKey}`}>
                <div className="previewGatePageBadge">
                  <strong>Physical p.{view.page.physical_page}</strong>
                  <span>label {view.page.page_label || "n/a"}</span>
                </div>
                <PdfLocationPreview
                  pdfUrl={pdfPreviewUrl}
                  page={view.page.physical_page}
                  chunkId={view.focusKey}
                  location={{
                    pdf_page: view.page.physical_page,
                    locator_status: view.rects.length ? "layout_line_location" : "page_level_only",
                    locator_reason: focusedCandidateMode
                      ? "Selected candidate OCR line coordinates."
                      : "OCR repair preview line coordinates.",
                    visual_mode: "layout_line_highlight",
                    page_width: view.page.page_width,
                    page_height: view.page.page_height,
                    highlight_count: view.rects.length,
                    rects: view.rects,
                  }}
                />
              </div>
          ))}
        </section>
      </div>

      <footer className="repairPreviewActions">
        <button type="button" className="primaryButton" onClick={onBuildPlan} disabled={planLoading || diagnosticOnly}>
          {diagnosticOnly ? "Repair plan is not recommended for this PDF. Use normal text-layer import." : (planLoading ? "Generating repair plan draft..." : "Continue to repair plan")}
        </button>
        <button type="button" onClick={onBackToNormal}>Back to normal import</button>
        <button type="button" onClick={onCancelReplace}>Cancel / replace PDF</button>
        <p>继续操作只会生成 sample-level plan draft，不启动正式导入或 OCR apply。</p>
      </footer>
      <RepairPlanDraftPanel result={planResult} loading={planLoading} error={planError} />
    </section>
  );
}

function buildRepairPdfViews(pages, activeCandidate, focusedCandidateMode) {
  if (!focusedCandidateMode || !activeCandidate) {
    return pages.map(page => ({
      page,
      rects: pageOverviewRects(page),
      focusKey: `overview:${page.physical_page}`,
      spansMultiplePages: false,
    }));
  }
  const selectedIds = new Set(candidateSourceLineIds(activeCandidate));
  const matchedViews = pages
    .map(page => ({
      page,
      rects: selectedCandidateRects(page, selectedIds),
      focusKey: `candidate:${activeCandidate.key}:${page.physical_page}`,
      spansMultiplePages: false,
    }))
    .filter(view => view.rects.length > 0)
    .sort((left, right) => right.rects.length - left.rects.length);
  const primaryPage = matchedViews[0]?.page || pages.find(page => page.physical_page === candidatePhysicalPage(activeCandidate));
  if (!primaryPage) return [];
  const primaryView = matchedViews[0] || {
    page: primaryPage,
    rects: selectedCandidateRects(primaryPage, selectedIds),
    focusKey: `candidate:${activeCandidate.key}:${primaryPage.physical_page}`,
  };
  return [{
    ...primaryView,
    spansMultiplePages: matchedViews.length > 1,
  }];
}

function pageOverviewRects(page) {
  return (page.ocr_lines || []).map(line => line.bbox).filter(Boolean);
}

function selectedCandidateRects(page, selectedIds) {
  return (page.ocr_lines || [])
    .filter(line => selectedIds.has(line.line_id || line.id))
    .map(line => line.bbox)
    .filter(Boolean);
}

function candidateSourceLineIds(candidate) {
  return candidate.source_line_ids || candidate.sourceLineIds || [];
}

function candidatePhysicalPage(candidate) {
  return candidate.physical_page || candidate.pdf_page || candidate.page?.physical_page || candidate.page;
}

function candidateSnippet(value) {
  const lines = String(value || "(no OCR text)").split(/\r?\n/).filter(Boolean);
  return lines.slice(0, 2).join(" ");
}

function CandidateDetail({ candidate, textMode, onTextModeChange, onBack }) {
  const previewText = textMode === "markdown" ? buildCandidateMarkdown(candidate) : buildCandidatePlainText(candidate);
  const correctedText = candidateRepairText(candidate);
  return (
    <section className="repairCandidateDetail" aria-label="Candidate repair detail">
      <div className="repairDetailHeader">
        <button type="button" className="repairDetailBack" onClick={onBack}>Back to candidates</button>
        <div className="repairDetailTitle">
          <div>
            <h4>Candidate {candidate.candidate_index}</h4>
            <span>Physical p.{candidate.physical_page} / label {candidate.page_label || "n/a"}</span>
          </div>
          <strong className={`repairStatus ${candidate.quality_status}`}>{candidate.quality_status}</strong>
        </div>
      </div>
      <div className="previewGateTabs" role="tablist" aria-label="Candidate detail preview format">
        <button type="button" className={textMode === "markdown" ? "active" : ""} onClick={() => onTextModeChange("markdown")}>Markdown</button>
        <button type="button" className={textMode === "text" ? "active" : ""} onClick={() => onTextModeChange("text")}>Plain text</button>
      </div>
      <div className="repairCandidateDetailBody">
        <pre className="repairDetailPreview">{previewText}</pre>
        <section className="repairDetailSection">
          <h5>Source lines</h5>
          <p>{(candidate.source_line_ids || []).length} OCR lines linked to this candidate.</p>
        </section>
        <section className="repairDetailSection">
          <h5>OCR original text</h5>
          <pre>{candidateOriginalText(candidate)}</pre>
        </section>
        <section className="repairDetailSection">
          <h5>Corrected preview text</h5>
          <pre>{correctedText}</pre>
        </section>
        <CorrectionList label="Safe corrections" items={candidate.safe_corrections} emptyLabel="No safe corrections suggested." />
        <CorrectionList label="Risky corrections / review required" items={candidate.risky_corrections} emptyLabel="No risky corrections suggested." />
        <ReasonList reasons={candidate.blocked_reasons} emptyLabel="No blocked or review reasons." />
      </div>
    </section>
  );
}

function candidateRepairText(candidate) {
  return candidate.corrected_preview_text
    || candidate.corrected_text
    || candidate.candidate_text
    || candidate.text
    || (candidate.source_line_texts || []).join("\n")
    || "(no candidate text)";
}

function candidateOriginalText(candidate) {
  return candidate.candidate_text
    || candidate.text
    || (candidate.source_line_texts || []).join("\n")
    || "(no OCR text)";
}

function buildCandidateMarkdown(candidate) {
  const safeCorrections = markdownCorrections(candidate.safe_corrections, "No corrections suggested.");
  const riskyCorrections = markdownCorrections(candidate.risky_corrections, "No corrections suggested.");
  const reasons = markdownItems(candidate.blocked_reasons, "No blocked or review reasons.");
  return [
    `# Candidate ${candidate.candidate_index}`,
    "",
    `**Status:** ${String(candidate.quality_status || "unknown").toUpperCase()}`,
    `**Page:** physical p.${candidate.physical_page} / label ${candidate.page_label || "n/a"}`,
    `**Source lines:** ${(candidate.source_line_ids || []).length}`,
    "",
    "## Repair Preview",
    "",
    candidateRepairText(candidate),
    "",
    "## Safe corrections",
    "",
    safeCorrections,
    "",
    "## Risky corrections / review required",
    "",
    riskyCorrections,
    "",
    "## Blocked reasons",
    "",
    reasons,
  ].join("\n");
}

function buildCandidatePlainText(candidate) {
  return [
    candidateRepairText(candidate),
    "",
    `Status: ${String(candidate.quality_status || "unknown").toUpperCase()}`,
    `Physical page: ${candidate.physical_page} / label ${candidate.page_label || "n/a"}`,
    `Source lines: ${(candidate.source_line_ids || []).length}`,
  ].join("\n");
}

function markdownCorrections(items = [], emptyLabel) {
  if (!items.length) return emptyLabel;
  return items.map(item => `- ${correctionText(item)}`).join("\n");
}

function markdownItems(items = [], emptyLabel) {
  if (!items.length) return emptyLabel;
  return items.map(item => `- ${item}`).join("\n");
}

function correctionText(item) {
  if (typeof item === "string") return item;
  return `${item.before || "raw text"} -> ${item.after || item.suggested || "manual review"}`;
}

function CorrectionList({ label, items = [], emptyLabel = "" }) {
  return (
    <div className="repairCandidateNotes">
      <span>{label}</span>
      {items.length ? items.map((item, index) => (
        <small key={`${label}-${index}`}>{correctionText(item)}</small>
      )) : <small>{emptyLabel}</small>}
    </div>
  );
}

function ReasonList({ reasons = [], emptyLabel = "" }) {
  return (
    <div className="repairCandidateNotes blocked">
      <span>Review reasons</span>
      {reasons.length ? reasons.map(reason => <small key={reason}>{reason}</small>) : <small>{emptyLabel}</small>}
    </div>
  );
}

function recommendationLabel(value) {
  return {
    repair_viable: "Sample OCR preview is viable for repair planning.",
    replace_pdf_recommended: "OCR preview is blocked; replacing the PDF is recommended.",
    manual_review_required: "OCR preview contains candidates requiring manual review.",
  }[value] || value || "Recommendation unavailable.";
}

function hasCleanPdfRepairWarning(result) {
  const values = [
    ...(Array.isArray(result.warnings) ? result.warnings : []),
    ...(Array.isArray(result.warning_codes) ? result.warning_codes : []),
  ];
  return values.includes("normal_text_layer_already_available_repair_not_recommended");
}

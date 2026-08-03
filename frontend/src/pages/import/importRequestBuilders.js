export function buildPreviewPayload({
  sourceMode,
  selectedImportRoute,
  importReadiness = {},
  pdfPath = "",
  convertedMdPath = "",
  titleHint = "",
  selectedZoteroSource,
}) {
  if (sourceMode === "converted_md" || selectedImportRoute === "converted_md" || importReadiness.recommended_route === "converted_md") {
    return {
      source_type: "converted_md",
      converted_md_path: sourceMode === "converted_md" ? pdfPath.trim() : convertedMdPath,
      title_hint: titleHint.trim() || undefined,
    };
  }
  if (sourceMode === "zotero_pdf") {
    return {
      source_type: "zotero_pdf",
      zotero_pdf_source_id: selectedZoteroSource.id,
      title_hint: titleHint.trim() || selectedZoteroSource.title || undefined,
    };
  }
  return {
    source_type: "local_pdf",
    pdf_path: pdfPath.trim(),
    title_hint: titleHint.trim() || undefined,
  };
}

export function buildClassifyPayload({
  sourceMode,
  selectedZoteroSource,
  pdfPath = "",
}) {
  if (sourceMode === "zotero_pdf") {
    return {
      source: "zotero",
      pdf_path: selectedZoteroSource.resolved_pdf_path || pdfPath.trim(),
      zotero_key: selectedZoteroSource.zotero_item_key || undefined,
      zotero_attachment_key: selectedZoteroSource.zotero_attachment_key || undefined,
      zotero_pdf_source_id: selectedZoteroSource.id,
    };
  }
  return {
    source: "local",
    pdf_path: pdfPath.trim(),
  };
}

export function buildPreviewGatePayload({
  sourceMode,
  selectedZoteroSource,
  pdfPath = "",
}) {
  const base = {
    sample_strategy: "first_chapter_first_section_two_pages",
    max_pages: 2,
  };
  if (sourceMode === "zotero_pdf") {
    return {
      ...base,
      zotero_attachment_path: selectedZoteroSource.resolved_pdf_path || pdfPath.trim(),
    };
  }
  return {
    ...base,
    pdf_path: pdfPath.trim(),
  };
}

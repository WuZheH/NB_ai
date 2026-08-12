import type { SourceType } from "../types";

export const SOURCE_LABELS: Record<SourceType, string> = {
  pdf_chunk: "PDF 原文",
  zotero_annotation_comment: "Zotero 批注",
  zotero_child_note: "Zotero 笔记",
  zotero_inspiration_note: "灵感笔记",
};

export function SourceBadge({ sourceType }: { sourceType: SourceType }) {
  return <span className={`source-badge search-source-badge search-source-${sourceType}`}>{SOURCE_LABELS[sourceType]}</span>;
}

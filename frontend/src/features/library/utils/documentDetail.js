export const NOTE_FILTERS = [
  { value: "all", label: "全部" },
  { value: "memory_note", label: "记忆" },
  { value: "connection_note", label: "连接" },
  { value: "mechanism_note", label: "机制" },
  { value: "research_idea_note", label: "研究想法" },
  { value: "evidence_only", label: "仅高亮证据" },
];

export const NOTE_TYPE_TAGS = new Set(NOTE_FILTERS.filter((filter) => filter.value !== "all").map((filter) => filter.value));

export const NOTE_TYPE_LABELS = {
  memory_note: "memory_note",
  connection_note: "connection_note",
  mechanism_note: "mechanism_note",
  research_idea_note: "research_idea_note",
  evidence_only: "仅高亮证据",
  zotero_inspiration_note: "zotero_inspiration_note",
};

export function noteKey(note) {
  return note.server_note_id || note.client_note_id || note.id || note.note_id || `${note.matched_document_id}-${note.matched_chunk_id}-${note.created_at}`;
}

export function noteTypeTags(note) {
  if (isEvidenceOnlyNote(note)) return ["evidence_only"];
  const tags = Array.isArray(note.user_tags) ? note.user_tags.filter(Boolean) : [];
  return tags.filter((tag) => NOTE_TYPE_TAGS.has(tag));
}

export function primaryNoteType(note) {
  return noteTypeTags(note)[0] || "zotero_inspiration_note";
}

export function noteTypeCounts(notes) {
  return notes.reduce((acc, note) => {
    noteTypeTags(note).forEach((tag) => {
      acc[tag] = (acc[tag] || 0) + 1;
    });
    return acc;
  }, {});
}

export function noteSort(a, b) {
  const pageA = a.pdf_page == null ? Number.POSITIVE_INFINITY : Number(a.pdf_page);
  const pageB = b.pdf_page == null ? Number.POSITIVE_INFINITY : Number(b.pdf_page);
  if (pageA !== pageB) return pageA - pageB;
  const pageLabel = String(a.page_label || "").localeCompare(String(b.page_label || ""));
  if (pageLabel) return pageLabel;
  const created = String(a.created_at || "").localeCompare(String(b.created_at || ""));
  if (created) return created;
  return String(a.client_note_id || "").localeCompare(String(b.client_note_id || ""));
}

export function notesSourceSummary(inspirationNotes, personalNotes) {
  const sources = new Set();
  if ((personalNotes || []).length) sources.add("PersonalNote");
  (inspirationNotes || []).forEach((note) => {
    if (note.source === "synthetic_acceptance_seed") sources.add("synthetic seed");
    else if (note.source === "zotero_native_annotation" && isEvidenceOnlyNote(note)) sources.add("仅高亮证据");
    else if (note.source === "zotero_native_annotation") sources.add("Zotero 原生笔记");
    else if (note.source === "zotero_plugin") sources.add("legacy desktop capture");
    else if (note.source) sources.add(note.source);
    else sources.add("Zotero");
  });
  if ((inspirationNotes || []).some((note) => note.evidence_alignment_status)) sources.add("aligned notes");
  return sources.size ? Array.from(sources).join(" / ") : "Zotero / synthetic seed / aligned notes";
}

export function noteSourceLabel(note) {
  const source = note?.source;
  if (source === "synthetic_acceptance_seed") return "测试 seed";
  if (source === "zotero_native_annotation" && isEvidenceOnlyNote(note)) return "仅高亮证据";
  if (source === "zotero_native_annotation") return "Zotero 原生笔记";
  if (source === "zotero_plugin") return "legacy desktop capture";
  return source || "Zotero note";
}

export function noteRoleLabel(note) {
  if (isEvidenceOnlyNote(note)) return "仅高亮证据";
  if (hasUserNoteText(note) && note?.source === "zotero_native_annotation") return "Zotero 原生笔记";
  if (hasUserNoteText(note)) return "用户笔记";
  return note?.note_processing_role || "blocked";
}

export function hasUserNoteText(note) {
  if (note?.has_user_note_text === true) return true;
  return String(note?.note_text || "").trim().length > 0;
}

export function isEvidenceOnlyNote(note) {
  if (note?.is_evidence_only === true) return true;
  if (note?.note_processing_role === "evidence_only") return true;
  return String(note?.selected_text || "").trim().length > 0 && !hasUserNoteText(note);
}

export function noteProcessingSummary(notes) {
  return (notes || []).reduce((acc, note) => {
    acc.annotationCount += 1;
    if (hasUserNoteText(note)) acc.userNoteCount += 1;
    if (isEvidenceOnlyNote(note)) acc.evidenceOnlyCount += 1;
    return acc;
  }, { annotationCount: 0, userNoteCount: 0, evidenceOnlyCount: 0 });
}

export function jsonListValue(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

export function noteMatchedChunkIds(note) {
  if (Array.isArray(note.matched_chunk_ids_json)) return note.matched_chunk_ids_json;
  if (Array.isArray(note.matched_chunk_ids)) return note.matched_chunk_ids;
  if (typeof note.matched_chunk_ids_json === "string") {
    try {
      const parsed = JSON.parse(note.matched_chunk_ids_json);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }
  return note.matched_chunk_id ? [note.matched_chunk_id] : [];
}


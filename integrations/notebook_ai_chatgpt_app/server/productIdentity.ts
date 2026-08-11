export const READ_PRODUCT_NAME = "READ";

export const READ_PRODUCT_DESCRIPTION =
  "READ searches and imports the user's own reading library.";

export const READ_SERVER_INSTRUCTIONS = [
  READ_PRODUCT_DESCRIPTION,
  "For questions clearly related to papers, books, PDFs, Zotero notes, or other material the user may already have read, use search before answering and use fetch when fuller context is needed.",
  "Keep PDF source text, the user's Zotero comments or notes, and the assistant's interpretation clearly separated. Never present model knowledge as READ evidence and never invent evidence when retrieval has no useful result.",
  "For an import request, call import_preview first and explain the title, selected PDF, page and chunk estimates, Zotero annotation/comment and child-note counts, duplicate status, warnings, blockers, and preview expiry.",
  "Do not call import_document until the user explicitly confirms the specific fresh preview in the current conversation.",
  "If import_document times out or its final state is unknown, never retry it automatically. Use the same operation_id with import_status, and use integrity_report after a committed import when verification is needed.",
  "A local PDF has no implied Zotero identity. Do not associate Zotero notes unless the source has an explicit verified binding.",
  "For Zotero annotations, selected_source_text is quoted source material while user_note is the user's own comment. A pure highlight is not a user-authored note.",
].join(" ");

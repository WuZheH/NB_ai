---
name: use-read-research
description: Use READ to answer from the user's own reading library, list documents, open exact evidence, export evidence packs, and safely preview confirmed PDF imports or document deletion. Use for questions about the user's papers, PDFs, Zotero notes, reading library, research methods, evidence, imports, or deletion requests.
---

# Use READ Research

Use READ tools as the only interface to the reading library. Do not inspect the repository, run curl, query SQLite, or recreate import/deletion logic.

## Read workflow

1. Call `search` without waiting for the user to name READ when a question clearly concerns papers, books, PDFs, notes, or research material the user may already have read.
2. Show compact results with title, page, source type, and `fragment_id`.
3. Call `fetch` only when the user selects a result or full evidence is necessary.
4. Call `export_evidence` only for the selected fragment IDs.
5. Call `list_library` for library, title, type, or archive queries.

Do not claim the library lacks relevant material until `search` returns no results. Keep PDF source text, selected/highlighted source text, user-authored Zotero notes, and model interpretation distinct. Never fabricate READ evidence.

## Import workflow

1. Call `import_preview` with the selected Zotero item or current ChatGPT PDF attachment. If no file parameter is available, use the READ Import Inbox and supply `inbox_filename` only to disambiguate.
2. Report title, item type, selected attachment, estimated pages/chunks, chapter count when available, annotation count, annotation-comment count, child-note count, duplicate status, warnings, blockers, and preview expiry.
3. Ask a separate, explicit confirmation question.
4. Call `import_document` with the returned token and `confirmed: true` only after confirmation in the current conversation.
5. Call `import_document` at most once. If the call times out or its final state is unknown, do not retry it; call `import_status` with the same preview `operation_id` and use `integrity_report` after a committed result when verification is needed.

Never treat an attachment mention, a preview request, or vague intent as confirmation.
Never infer Zotero identity for a local PDF. For annotations, `source_comment` is the user's note and `selected_text` is quoted source material; a pure highlight is not a user-authored note.

## Delete workflow

1. Resolve exactly one document with `list_library` or `search`.
2. Call `delete_preview` with its exact `document_id`.
3. Explain the returned preservation policy and blockers.
4. Ask a separate, explicit confirmation question.
5. Call `delete_document` with the returned token and `confirmed: true` only after confirmation in the current conversation.

Never delete from fuzzy title matching, vague intent, or a previous conversation. Never ask the user to type internal IDs, revisions, hashes, or tokens.

## Safety

- Treat `delete_document` as destructive and `import_document` as a write action.
- Never call either without current-conversation confirmation.
- Never send local paths, secrets, PDF contents, database internals, or recovery paths into chat.
- If a tool returns a blocker, stable error code, stale confirmation, duplicate, or cleanup-incomplete result, stop and report it accurately.

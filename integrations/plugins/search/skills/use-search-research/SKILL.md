---
name: use-search-research
description: Use the Search App to answer questions from the user's private local research library, list documents, open exact evidence, export evidence packs, and safely preview confirmed PDF imports or document deletion. Use for questions about the user's papers, PDFs, Zotero notes, reading library, research methods, evidence, imports, or deletion requests.
---

# Use Search Research

Use Search tools as the only interface to Search Core. Do not inspect the repository, run curl, query SQLite, or recreate import/deletion logic.

## Read workflow

1. Call `search` for questions about research content.
2. Show compact results with title, page, source type, and `fragment_id`.
3. Call `fetch` only when the user selects a result or full evidence is necessary.
4. Call `export_evidence` only for the selected fragment IDs.
5. Call `list_library` for library, title, type, or archive queries.

Do not claim the library lacks relevant material until `search` returns no results. Keep PDF source text distinct from user-authored Zotero notes.

## Import workflow

1. Call `import_preview` with the current ChatGPT PDF attachment. If no file
   parameter is available, use the Search Import Inbox and supply
   `inbox_filename` only to disambiguate.
2. Report title, hash, duplicate status, estimated pages/chunks, type, and warnings.
3. Ask a separate, explicit confirmation question.
4. Call `import_document` with the returned token and `confirmed: true` only after confirmation in the current conversation.

Never treat an attachment mention, a preview request, or vague intent as confirmation.

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

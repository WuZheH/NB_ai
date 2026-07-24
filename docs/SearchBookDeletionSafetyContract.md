# Search Book Deletion Safety Contract

## 1. Product boundary

Search exposes two different actions:

- **Archive / remove from shelf** changes `documents.read_status` to `archived`. Archived documents are omitted from the active shelf and from default keyword, high-quality, and vector source collection. The previous `read` or `mastered` value is stored in `library_archive_states` and can be restored.
- **Delete Search data** removes one explicitly identified document and its exclusive derived data. It never uses a title lookup, fuzzy match, GET mutation, default selection, or “clear shelf” action.

`library_archive_states` is created lazily inside the first explicit archive transaction. Runtime startup, deletion preview, packaged smoke, and cold-start checks do not migrate an existing production database.

Research Workspace remains implemented and reachable by direct `/workspace` deep link. Sidebar, shelf, document detail, import completion, fallback shell, menus, and ordinary shortcuts do not expose a Workspace entry.

## 2. Local API contract

The canonical routes are:

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/v1/library/management/mutation-session` | Issue a short-lived local Desktop mutation token |
| POST | `/api/v1/library/management/archive` | Archive one to five explicit document IDs |
| POST | `/api/v1/library/management/restore` | Restore one to five explicit document IDs |
| GET | `/api/v1/library/documents/{document_id}/deletion-preview` | Read-only deletion impact preview |
| POST | `/api/v1/library/documents/{document_id}/delete` | Confirm and delete one document |
| POST | `/api/v1/library/documents/delete-batch` | Confirm one to five independently previewed documents |

The preview returns the document ID and title, document type and source kind, redacted PDF descriptor and hash, PDF existence and managed/external scope, chunk/chapter/node counts, object/shared/exclusive counts, evidence and note counts, FTS rows, passage and object-vector impact, manifest impact, derived files, generated cache, blockers, warnings, estimated rows/files, retention choices, revision hash, preview hash, expiring token, and `whether_safe_to_delete`.

No API response exposes a complete local path. Path-bearing values are limited to basename, managed/external scope, path hash, existence, and file hash where required.

## 3. Preview and confirmation

Preview is opened through SQLite read-only URI connections, a read-only FTS connection, and exact LanceDB source-ID inspection. It does not create the archive root, migrate the database, update a token on disk, rebuild an index, or write audit data.

Every delete request includes:

- exact `document_id` in both URL and body;
- the expiring preview token;
- expected document revision;
- confirmation text equal to the exact title or `删除`;
- the exact options that were previewed.

The backend reacquires a process-wide deletion mutex, regenerates the complete impact, and rejects a changed revision, changed options, changed impact hash, expired/used token, wrong confirmation, missing document, or blocker. After SQLite grants `BEGIN IMMEDIATE`, it regenerates and compares the impact once more before the first mutation, closing the preview-to-write-lock race window. Tokens are one-use for mutation. Batch deletion has a hard limit of five, verifies the exact selected ID list, preflights every document before starting, and rejects selected documents that share an object key because the first deletion would change the second preview.

## 4. Cascade and retention policy

SQLite `ON DELETE CASCADE` is not used as an assumption. The service enumerates and orders each supported table operation.

Deleted exclusive data includes document rows, chunks, Markdown nodes, book chapters, document-source links, chunk tags, chunk/layout links, PDF layout caches, OCR derived rows, exclusive object-candidate rows, FTS fragments after rebuild, passage vectors, exclusive object vectors, document-scoped generated Markdown, and explicitly allowed generated cache files.

Protected data is handled as follows:

- external PDF: always retained;
- Search-managed PDF: retained by default and deletable only when the previewed option is enabled and no other document refers to it;
- personal notes: retained and `document_id` detached; evidence links to removed chunks are removed;
- Zotero snapshot/library/original item: never modified;
- Zotero inspiration notes: retained; document, chunk, and removed-object associations are detached, including notes whose only association is `matched_object_ids_json`;
- shared object candidates and vectors: other document rows remain; affected shared vectors are re-embedded from the surviving source;
- inspiration cards: retained; only invalid source links are removed;
- knowledge relations: retained and invalid evidence-chunk association detached;
- Evidence Basket and Right Inspector: invalid renderer-memory selection is cleared after a successful refresh.

An object row with a user comment, any protected chapter/note/object review artifact, a cross-document reference to a target chunk, an unknown schema reference, unreadable FTS impact, unavailable vector impact, or unsafe retention option is a blocker. The service does not silently delete or overwrite these records.

## 5. Transaction and derived cleanup

The order is:

1. regenerate and validate the preview;
2. create and verify the recovery package;
3. acquire `BEGIN IMMEDIATE` with foreign keys enabled;
4. detach retained notes/relations;
5. delete explicit link and derived rows;
6. delete chunks, nodes, chapters, archive state, and document;
7. run `foreign_key_check` and commit;
8. rebuild the derived FTS database and manifest;
9. delete exact passage vectors and reconcile only affected object keys;
10. delete only previewed, allowed managed files;
11. scan database, FTS, vectors, notes, and document-object links for orphans;
12. write a sanitized audit result.

Any database error rolls back fully and does not touch FTS, vectors, or files. A post-commit FTS/vector/file/orphan failure returns `cleanup_incomplete` with stable codes and remediation; it never reports complete success.

## 6. Recovery and audit

Before the database transaction, Search creates one minimal package below the configured archive root. The default canonical layout resolves outside `SEARCH_DATA_DIR`, under `Archives/SearchBookDeletion/<audit-id>`.

The package contains:

- structured rows that will be deleted or detached;
- document/chunk/relation metadata;
- cleanup identities for FTS/vector reconciliation;
- file hashes and managed-file cleanup targets;
- the redacted preview and deletion options;
- the final sanitized deletion report.

It does not copy an external PDF, unrelated production rows, credentials, tokens, machine configuration, or note text into the audit report. The recovery package may contain deleted source row content required for restoration and remains local outside the canonical data root.

`scripts/maintenance/reconcile_search_book_deletion.py` is dry-run by default. It requires an exact audit ID; `--apply` retries only post-commit FTS/vector/file cleanup after proving the document no longer exists. It cannot substitute for the database delete transaction and rejects file targets outside the configured data roots.

## 7. Local-only authorization

Mutation and preview routes require a loopback client, a loopback API Host, an exact local renderer Origin, no Forwarded/Cloudflare headers, a bounded request size, and per-scope rate limits. Archive/delete POST requests additionally require a short-lived mutation token bound to client and Origin. Public/tunnel calls are rejected before service execution.

The MCP server remains limited to `search`, `fetch`, and `export_evidence`. No archive, preview, delete, cleanup, or recovery tool is registered for MCP or the ChatGPT App.

## 8. Verification contract

All mutation tests use temporary SQLite databases, temporary data roots, temporary vector doubles, and temporary archive roots. Coverage includes read-only preview, missing documents, invalid/stale tokens, revision changes, wrong confirmation, shared-object preservation, user-comment/review blockers, personal and Zotero note detach, external and managed PDF behavior, FTS/vector cleanup, rollback before derived cleanup, `cleanup_incomplete`, orphan scan, recovery package, retry dry-run/apply, batch limit/overlap, local-only security, MCP non-exposure, UI second confirmation, shelf refresh, and Workspace deep-link retention.

Production acceptance is preview-only. Candidate10 validation must not send a final delete request for any production document and must prove the production tree hash, SQLite integrity, foreign keys, and WAL/SHM state are unchanged.

The pre-package source baseline on 2026-07-24 passed:

- Core: 243 tests;
- frontend: 45 tests;
- Desktop: 76 tests;
- MCP widget/server: 25 tests, with exactly the three read-only tools;
- Python compile, Vite production build, MCP widget build, and MCP server build;
- isolated helper seed, safe preview, committed deletion, orphan scan, foreign-key check, and one complete recovery package;
- secret scan and MCP delete-exposure scan with zero findings;
- production read-only guard: 191 files, 670,300,309 bytes, 41/41 SQLite integrity checks, zero foreign-key issues, zero WAL/SHM files, and the unchanged `search.tree-hash.v1` digest.

Candidate10 packaged smoke must repeat the real HTTP read-shelf, archive/restore, preview, local mutation-token, isolated delete, recovery-package, `/workspace` deep-link, single-instance, and controlled-exit checks against a unique non-production data root. Its result belongs in the Candidate10 completion report and is not assumed by this contract.

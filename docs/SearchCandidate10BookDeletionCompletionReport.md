# Search Candidate10 Safe Book Deletion Completion and Rollback Report

## 1. Outcome

Candidate10 implemented and validated the safe local book archive/deletion workflow and removed ordinary Research Workspace entry points. Source regression, isolated mutation tests, build, package identity checks, and the isolated packaged smoke passed.

Candidate10 was **not** accepted as the formal desktop release. Formal cold-start rounds 1 and 2 passed, but round 3 returned `model_load_failed` from high-quality search. The following MCP `search` call returned an error payload that did not match its declared output schema, and the MCP client rejected it with `-32602`.

The failure was not hidden by a retry. Candidate10 was gracefully exited, production data was revalidated, and the original `Search Desktop` Scheduled Task XML was restored. Candidate9 is the active formal package and passed keyword search, high-quality search, and the three-tool MCP check after rollback.

No production book was archived or deleted. Candidate10 is not ready for user book deletion in the formal UI.

## 2. Repository and commits

| Item | Value |
| --- | --- |
| Repository | `WuZheH/NB_ai` |
| Branch | `codex/search-canonical-root-migration` |
| Candidate9 baseline | `3b6c95a8332442dd5d6036de0ebeafe9cbafa23c` |
| Safe-deletion feature commit | `a7cd6eb6970f6c0b810f02702b13cf135a74aa28` |
| Candidate10 source commit | `d8ed1b64931a2867df74cd4fe00a0eae87c5579e` |
| Feature commit URL | `https://github.com/WuZheH/NB_ai/commit/a7cd6eb6970f6c0b810f02702b13cf135a74aa28` |
| Candidate10 source URL | `https://github.com/WuZheH/NB_ai/commit/d8ed1b64931a2867df74cd4fe00a0eae87c5579e` |
| Report commit | The Git commit containing this document; the authoritative 40-character hash is recorded in the final handoff because a commit cannot contain its own hash. |
| Ahead / behind before report commit | `0 / 0` |
| Tracked state before report commit | clean |

The feature diff contains 32 files with 4,373 insertions and 106 deletions. The follow-up Windows build-tool commit changes one file with two insertions and one deletion. No existing source file was deleted.

## 3. Implemented product behavior

### 3.1 Workspace entry removal

Ordinary user entry points were removed from:

- Sidebar;
- read-shelf header;
- book cards;
- document and book detail actions;
- import completion and fallback shell;
- ordinary menus and shortcuts.

The remaining `Research Workspace` strings are internal Workspace implementation/title values and direct-route code. `ResearchWorkspacePage`, `NotebookWorkspaceShell`, Workspace backend behavior, and Workspace tests remain present. Direct `/workspace` and document/chapter deep links remain loadable.

Static and frontend tests prove:

- no Sidebar Workspace entry;
- no shelf header or book-card Workspace action;
- no ordinary detail-page Workspace button;
- `/workspace` route and source modules remain;
- the default desktop route remains `/retrieval`.

### 3.2 Shelf management

The read shelf now has:

- `导入书籍`;
- `管理书架`;
- `刷新书架`;
- a management mode with no default selection;
- explicit selection count and a maximum of five documents;
- independent preview for every selected document;
- archive, archived view, restore, preview, and permanent-delete actions;
- selection reset after refresh/success.

Archive is reversible and stores the previous `read` or `mastered` state in `library_archive_states`. Active shelf and default search exclude archived documents. Archive is not implemented as deletion.

### 3.3 Preview and permanent deletion

The read-only route is:

`GET /api/v1/library/documents/{document_id}/deletion-preview`

It returns redacted source/PDF identity, document revision, counts for chunks, chapters/nodes, object links, shared/exclusive objects, evidence, personal/Zotero notes, FTS, passage/object vectors, manifest/cache/files, blockers, warnings, estimates, retention policy, preview hash, expiring token, and `whether_safe_to_delete`.

Permanent deletion requires:

- exact document ID in URL and body;
- loopback Desktop request validation;
- exact local renderer Origin;
- local mutation token;
- fresh one-use preview token;
- unchanged revision/options/impact hash;
- exact title or `删除` confirmation;
- no blockers;
- POST request with bounded size and rate limits.

MCP remains exactly `search`, `fetch`, and `export_evidence`. No preview, archive, delete, cleanup, or recovery tool is exposed through MCP, ChatGPT App, or tunnel paths.

## 4. Data protection and transaction behavior

The transaction service is implemented in:

`app/services/library/document_deletion_service.py`

It creates and verifies a minimal recovery package before `BEGIN IMMEDIATE`, regenerates impact after obtaining the write lock, explicitly detaches protected notes and relations, removes document-exclusive rows, verifies foreign keys, commits SQLite, then reconciles FTS, vectors, allowed managed files, and orphans.

Protection rules verified by isolated tests:

- external PDFs are always retained;
- Search-managed PDFs are retained by default;
- personal notes are retained and detached;
- Zotero source data is never deleted;
- Zotero notes are retained and associations detached;
- shared objects and surviving shared vectors are protected;
- only exclusive derived objects may be removed;
- user comments, protected review artifacts, unsafe cross-document references, and unknown relations block deletion;
- database failure rolls back before file/vector changes;
- post-commit FTS/vector/file failure returns `cleanup_incomplete`;
- recovery reconciliation is dry-run by default and requires an exact audit ID.

## 5. Test and build evidence

### 5.1 Source regression

| Suite | Result |
| --- | --- |
| Core | 243 passed |
| Frontend | 45 passed |
| Desktop | 76 passed |
| MCP widget/server | 25 passed |
| Python compile | passed |
| Vite production build | passed |
| MCP widget/server build | passed |
| Secret scan | zero findings |
| MCP delete-exposure scan | zero findings |

The isolated deletion helper used a temporary database, data root, vector index, and archive root. It seeded document `910001`, completed a real isolated deletion, produced a recovery package, reported no orphan or foreign-key issue, and did not reference production data.

### 5.2 Candidate10 identity

| Item | Value |
| --- | --- |
| Build ID | `20260723-search-0.1.4-canonical-root-candidate10` |
| Candidate | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c10` |
| Smoke copy | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c10` |
| Retained formal copy | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-d8ed1b64` |
| Files | 423 |
| Bytes | 316,668,541 |
| `search.tree-hash.v1` | `4D2301EDCCD604613B65A888A23FE671DEDFA0DBA2CCFD885A345029E45DD295` |
| `Search.exe` SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |

Candidate, smoke copy, and formal copy were kept separate. Candidate1-9, Candidate9 formal, and old smoke directories were not overwritten or deleted. Production data, machine configuration, and desktop-runtime configuration were not bundled.

### 5.3 Packaged smoke

The valid packaged smoke passed against an isolated data root:

- managed-by-Search runtime ownership;
- `/retrieval` renderer;
- read shelf;
- `/workspace` deep link;
- archive and restore;
- deletion preview;
- mutation-session protection;
- real isolated transaction deletion;
- recovery package verification;
- single-instance reuse;
- controlled graceful exit;
- zero residual runtime processes/ports;
- no cloudflared change.

The smoke did not call a deletion endpoint against production.

## 6. Formal cold starts and rollback

| Round | Candidate10 ready | Read shelf / preview / PDF | Keyword / high-quality | MCP | Graceful exit | Production hash |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | passed | passed | passed | passed | passed | unchanged |
| 2 | passed | passed | passed | passed | passed | unchanged |
| 3 | passed | passed | keyword passed; high-quality failed | failed after high-quality model failure | passed | unchanged |

Round 3 exact stable evidence:

- FastAPI `/health`: `ok`;
- runtime state: `local_ready_tunnel_missing`;
- health machine-config status: `model_load_failed`;
- high-quality status: `high_quality_search_error`;
- MCP client: `-32602 structured_content_output_schema_mismatch`;
- database write flag: false;
- vector write flag: false;
- production deletion called: false.

Runtime status initially retained `embedding_model_ready=true` and `reranker_model_ready=true`, while the backend health detail had already changed to `model_load_failed`. This inconsistency and the MCP error-envelope/schema mismatch require a separate model-readiness/MCP error-contract investigation. They are not silently fixed with retry logic in the book-deletion task.

After the failure:

1. the user used the real tray `完全退出`;
2. Candidate10 processes and 5173/8000/8787 reached zero;
3. production integrity was revalidated;
4. the original Candidate9 Scheduled Task XML was restored;
5. task arguments remained empty;
6. Candidate9 was started from its formal directory;
7. Candidate9 keyword search, high-quality search, and MCP `search`/`fetch`/`export_evidence` passed.

Candidate10 candidate, smoke, formal copy, recovery evidence, and failure evidence remain available. No force termination was used.

## 7. Production guard

| Check | Baseline | After Candidate10 exit |
| --- | ---: | ---: |
| Files | 191 | 191 |
| Bytes | 670,300,309 | 670,300,309 |
| `search.tree-hash.v1` | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` | unchanged |
| SQLite integrity | 41/41 `ok` | 41/41 `ok` |
| Foreign-key issues | 0 | 0 |
| WAL/SHM | 0 | 0 |

Production validation only viewed the shelf and deletion preview. It never requested a production mutation session, archive, restore, batch delete, or document delete. No production document, note, PDF, FTS row, vector, manifest, or cache was changed.

## 8. User-visible availability

The Candidate10 management and deletion UI is implemented in source and passed isolated/package checks, but it is not the active formal UI because Candidate10 failed the third cold-start gate. The active formal package is Candidate9.

Therefore the user should **not** attempt production book deletion yet. After a successor candidate resolves and validates the model-readiness/MCP error contract, the intended UI flow is:

1. open `已读书架`;
2. choose `管理书架`;
3. select at most five books;
4. use `查看删除影响`;
5. review blockers, retained notes/PDFs/shared data, vectors, rows, and recovery summary;
6. type the exact title or `删除`;
7. choose `永久删除此书的 Search 数据`.

Actual deletion remains a user action after a formally accepted build.

## 9. Status markers

Passed in source, isolated tests, or packaged smoke:

- `PASS_SEARCH_WORKSPACE_UI_ENTRY_REMOVAL`
- `PASS_SEARCH_WORKSPACE_DEEP_LINK_RETAINED`
- `PASS_SEARCH_BOOK_ARCHIVE_WORKFLOW`
- `PASS_SEARCH_BOOK_DELETION_PREVIEW`
- `PASS_SEARCH_BOOK_DELETION_TRANSACTION`
- `PASS_SEARCH_SHARED_OBJECT_PROTECTION`
- `PASS_SEARCH_NOTE_AND_PDF_RETENTION`
- `PASS_SEARCH_VECTOR_AND_FTS_CLEANUP`
- `PASS_SEARCH_DELETION_RECOVERY_ARCHIVE`
- `PASS_SEARCH_DELETE_API_LOCAL_ONLY`
- `PASS_SEARCH_CANDIDATE10_BUILD`
- `PASS_SEARCH_NO_PRODUCTION_DATA_DRIFT`

Formal release result:

- `FAIL_SEARCH_CANDIDATE10_THREE_COLD_STARTS`
- `PASS_SEARCH_CANDIDATE9_ROLLBACK`
- `NOT_READY_FOR_USER_BOOK_DELETION`

The markers `PASS_SEARCH_CANDIDATE10_THREE_COLD_STARTS` and `READY_FOR_USER_BOOK_DELETION` are intentionally not emitted.

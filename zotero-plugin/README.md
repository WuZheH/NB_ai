# NOTEBOOK_AI Zotero Inspiration Capture MVP

## Objective

This Zotero 9.0.4-targeted plugin captures a user's reading-time
`inspiration_note` from a PDF Reader context. The intended action label is
`记下灵感`: a compact quick note records the selected source text, the user's
own note, tags, and any available Zotero/PDF anchor.

The product boundary from Phase 110K-A is unchanged:

- `inspiration_note` is authored by the user and retained as raw provenance.
- `mechanism_draft` is a later, higher-level backend artifact grounded in
  inspiration, evidence, and related objects.
- Object review occurs before mechanism review.
- This plugin only captures inspiration in the reading context.

## Explicit Non-Goals

This MVP plugin:

- does not generate mechanisms;
- does not call an LLM, OpenAI, ChatGPT, or any external AI service;
- does not write the NOTEBOOK_AI DB or any SQLite database;
- does not write `knowledge_chunks`, LanceDB, or a vector store;
- does not modify a PDF;
- does not overwrite or rewrite native Zotero annotation content;
- does not run OCR, Marker, or a PDF import.

`note_text`, `selected_text`, and every string in `user_tags` are user/source
material. They are saved exactly as supplied to the payload and are never
replaced with generated text.

## Skeleton Layout

```text
zotero-plugin/
  README.md
  MANIFEST_PLAN.md
  CAPABILITY_MATRIX.md
  manifest.json
  bootstrap.js
  src/
    inspirationQuickNote.js
    inspirationSidebar.js
    inspirationStore.js
    syncClient.js
    zoteroReaderBridge.js
```

This is a no-build, bootstrapped extension. It deliberately does not
add a package manager or third-party dependency.

## Quick Note Behavior

Preferred entry: the Reader text-selection popup or annotation context action
named `记下灵感`, registered using Zotero's documented Reader event-listener
API.

Fallback entry: `Tools` > `Notebook AI Inspiration: Capture Selection with Prompt`
or the public `captureSelectionWithPromptFallback()` method. In Zotero 9.0.4,
selection capture has been manually observed while the DOM popup host was not
available from `Run JavaScript`; the prompt route keeps capture and save
usable in that context.

The popup skeleton exposes:

- read-only selected-text preview;
- `note_text` textarea;
- one-tag-per-line tags textarea, preserving each entered tag string;
- `selection_type`: `sentence`, `paragraph`, `section_title`,
  `chapter_title`, or `manual`;
- save, cancel, and sync status elements.

When the popup host is unavailable, the plugin prompts for `note_text` and
comma-separated tags through `Services.prompt.prompt()`. The prompt tags
default to `__kl_real_capture_test__, 灵感` for disposable initial validation.
Comma-separated values are split without rewriting each entered value.

Saving first invokes the local pending store, then the Zotero HTTP-first
loopback sync client. If sync fails, the saved note remains in the local queue
as `sync_failed` and can be retried.

## Inspiration Note Payload

The plugin-side capture payload is:

```json
{
  "client_note_id": "zinsp_client_...",
  "source": "zotero_plugin",
  "zotero_item_key": "ITEM123",
  "zotero_attachment_key": "ATTACH123",
  "zotero_annotation_key": null,
  "pdf_page": null,
  "page_label": null,
  "selected_text": "Original selection",
  "selected_text_hash": "sha256:...",
  "note_text": "Original user note",
  "user_tags": ["灵感"],
  "selection_type": "paragraph",
  "context_before": null,
  "context_after": null,
  "bbox": null,
  "created_at": "2026-05-26T00:00:00.000Z",
  "updated_at": "2026-05-26T00:00:00.000Z",
  "sync_status": "local_pending"
}
```

`selected_text_hash` is stable SHA-256 over an NFC-normalized comparison
form with normalized line endings, collapsed horizontal whitespace, and
trimmed outer whitespace. The original `selected_text` is stored unchanged;
normalization is used only for the deduplication aid.

`pdf_page`, when available, means the physical 1-based PDF page.
`page_label` is display metadata only. `bbox` is retained only if a verified
anchor provides it; the plugin does not invent coordinates.

Zotero Reader selection rect arrays are normalized for the backend request as:

```json
{
  "format": "zotero_reader_rects_v1",
  "source": "zotero_reader_selection",
  "pdf_page": 235,
  "page_label": "235",
  "rects": [[0, 0, 1, 1]]
}
```

The actual coordinate numbers are passed through unchanged. Capture
diagnostics may still show the raw Reader array; only outbound `payload.bbox`
is required to satisfy the backend object contract.

## Local Pending Store

`src/inspirationStore.js` defines:

- `upsertNote(note)`;
- `listNotesByAttachment(attachmentKey)`;
- `markSynced(clientNoteId, serverResponse)`;
- `markFailed(clientNoteId, error)`;
- `retryPending(syncClient)`;
- `deduplicate()`.

The intended Zotero persistence adapter stores JSON queue data in a
plugin-namespaced preference. Because that adapter has not been manually
validated against the installed Zotero version, it is guarded and falls back
to in-session memory storage when unavailable. That fallback is explicit:
cross-restart pending persistence remains a manual validation target.

Deduplication examines, in order:

1. `zotero_attachment_key + zotero_annotation_key`;
2. `client_note_id`;
3. `zotero_attachment_key + selected_text_hash + created_at` five-minute
   bucket;
4. `zotero_attachment_key + pdf_page + selected_text_hash`.

The MVP reports duplicate groups and records conflicts; it does not silently
replace a distinct note. Later phases must define user-visible revision and
conflict resolution.

The store provides `listPendingNotes()` and `syncPendingNotes()` in addition
to per-attachment listing. It first attempts a Zotero-namespaced preference
store and falls back to session memory if that storage path is unavailable.
Memory fallback means notes survive within the running session but are not
claimed to survive restart.

Duplicate detection adds local `client_diagnostics` while leaving a newly
saved note pending for upload. Before HTTP submission, the sync client builds
an allowlisted K-C request with `sync_status=local_pending`, so local fields
such as `conflict_with_client_note_id` are never posted to the backend.
The outbound adapter also normalizes bbox for older pending notes saved before
Fix2, allowing them to be retried without deleting local user data.

## Sidebar Behavior

K-M adds a notes panel MVP backed by the read-only localhost endpoint for the
active attachment. Use `openInspirationSidebar()` or the Tools menu action.
After Zotero 9.0.4 manual validation showed that the available main-window
document had no usable HTML panel body, K-M-Fix1 adds a standalone dialog
fallback. If a standard document host is absent, `openInspirationSidebar()`
attempts a dedicated window and returns `ui_mode=dialog_fallback`; only if
that cannot be opened does it try the main-window root as
`ui_mode=window_fallback`. If neither host is usable it returns
`sidebar_host_unavailable` while the remote listing API remains usable.

K-N presents the dialog fallback as a scrollable dark panel titled
`Notebook AI Inspirations`, with loading, empty, and request-error states.
It groups notes by physical PDF page, displaying a group label such as
`Page 213 / PDF 243`, and sorts by `pdf_page` followed by capture timestamp.
Chapter grouping is deliberately deferred until NOTEBOOK_AI supplies an
approved chapter mapping.

Cards display `note_text`, selected-text snippet, tags, page information,
`sync_status`, `mechanism_status`, optional match/review status, and one of
these anchor states. Display tags are trimmed for readability without
rewriting transmitted or stored note fields:

- `annotation_anchor`;
- `page_anchor`;
- `manual_anchor`;
- `unmatched`.

Clicking a card or its jump button resolves that current remote note by
`client_note_id`, then attempts physical-page navigation. K-M-Fix1 first tries
the selected/active Zotero Reader's documented-in-practice
`reader.navigate({pageIndex})` path, converting the stored 1-based
`pdf_page` to a 0-based `pageIndex`. If no matching Reader is open it looks up
the attachment item, opens it using `Zotero.Reader.open(attachment.id)`, waits
briefly for the Reader instance, and retries page navigation. Existing adapter
and `navigateToPage`/`setPage`/`openPage` probes remain fallbacks. When
`bbox.rects` is available it separately attempts a temporary highlight hook.
Missing or unsupported hooks return
`reader_navigation_unavailable` and/or `bbox_highlight_unavailable`; the
plugin does not claim a highlight was displayed unless a hook ran
successfully.

## Localhost Sync Client

The only accepted default endpoint is:

```text
http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/upsert
```

K-M uses the same HTTP-first localhost adapter for read-only sidebar listing:

```text
GET http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/by-attachment/<encoded_attachment_key>
```

The client rejects endpoints whose hostname is not `127.0.0.1` or
`localhost`, and accepts only plain loopback HTTP in this skeleton. It is
configured as dry-run by default. Live smoke sync first uses
`Zotero.HTTP.request` with a 10-second timeout when that API is present, then
falls back to `fetch` only when Zotero's client is unavailable. A live upsert
expects:

```json
{
  "status": "OK",
  "server_note_id": "...",
  "sync_status": "synced",
  "matched_document_id": null,
  "matched_chunk_id": null
}
```

When the backend is unavailable, the local note is not discarded: it remains
`local_pending` before an attempt or becomes `sync_failed` after a failed
attempt and is eligible for `retryPending()`. The plugin never opens a
NOTEBOOK_AI database directly.

## Zotero 9 Manual Smoke Entry

At startup the plugin records:

```text
[NOTEBOOK_AI Inspiration] startup
[NOTEBOOK_AI Inspiration] smoke API registered
```

The stable manual smoke API is registered at
`Zotero.NotebookAIInspirationPlugin`. In Zotero's `Run JavaScript` window,
diagnose the registration, verify the loopback backend, and then submit only
the fixed smoke payload with:

```javascript
return Zotero.NotebookAIInspirationPlugin.getStatus();
return await Zotero.NotebookAIInspirationPlugin.checkBackendStatus();
return await Zotero.NotebookAIInspirationPlugin.runManualSmokeSync();
```

`getStatus()` returns `plugin_loaded`, `smoke_api_registered`,
`sync_endpoint`, and plugin `version`. The plugin also attempts a compatibility
exposure at `globalThis.NOTEBOOK_AI_INSPIRATION_PLUGIN`, permitting:

```javascript
return await NOTEBOOK_AI_INSPIRATION_PLUGIN.runManualSmokeSync();
```

If the compatibility name is not defined while the Zotero namespace exists,
the difference is Zotero `Run JavaScript` global scoping, not a plugin
failure. The equivalent menu action is available under `Tools` as
`Notebook AI Inspiration: Run Smoke Sync`; it invokes the same
`runManualSmokeSync()` method and records the outcome in Zotero debug output.

For localhost troubleshooting, check the backend outside Zotero first:

```powershell
Invoke-RestMethod -UseBasicParsing http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/sync-status
```

Then run `checkBackendStatus()` in Zotero before issuing the smoke POST. If
PowerShell succeeds while Zotero fails, the failure is in Zotero's local HTTP
request path. If PowerShell fails too, the backend is unavailable. Status and
smoke responses report `client`, `endpoint`, `http_status`, and `error`; a
failed smoke POST retains the queued note as `sync_failed`.

## Real Capture Entry Points

The public API for capture diagnostics and fallback use is:

```javascript
return Zotero.NotebookAIInspirationPlugin.captureCurrentSelection();
return await Zotero.NotebookAIInspirationPlugin.openQuickNoteForCurrentSelection();
return await Zotero.NotebookAIInspirationPlugin.captureSelectionWithPromptFallback();
return Zotero.NotebookAIInspirationPlugin.listLocalNotesForCurrentAttachment();
return await Zotero.NotebookAIInspirationPlugin.syncPendingNotes();
return await Zotero.NotebookAIInspirationPlugin.listRemoteNotesForCurrentAttachment();
return await Zotero.NotebookAIInspirationPlugin.openInspirationSidebar();
return await Zotero.NotebookAIInspirationPlugin.refreshInspirationSidebar();
return await Zotero.NotebookAIInspirationPlugin.jumpToNoteByClientId("<client_note_id>");
```

Reader capture produces `zotero_item_key`, `zotero_attachment_key`,
`zotero_annotation_key`, `pdf_page`, `page_label`, `selected_text`,
`context_before`, `context_after`, `bbox`, `anchor_status`,
`capture_method`, and `warnings`. Selection or annotation text is copied
without trimming or rewriting. When a Reader field is unavailable, its value
remains `null`/empty and `warnings` states the fallback rather than reporting
a false precise anchor.

Tools menu entries:

- `Notebook AI Inspiration: Capture Current Selection`
- `Notebook AI Inspiration: Capture Selection with Prompt`
- `Notebook AI Inspiration: List Current Attachment Notes`
- `Notebook AI Inspiration: Open Notes Sidebar`
- `Notebook AI Inspiration: Refresh Notes`
- `Notebook AI Inspiration: Run Smoke Sync`

## Reader API Status And Fallbacks

The capability spike is recorded in [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md).
The plugin registers `renderTextSelectionPopup` and
`createAnnotationContextMenu` using the official Zotero 7+ Reader API
documented at
<https://www.zotero.org/support/dev/zotero_7_for_developers#custom_reader_event_handlers>.
This API source does not change the supported target: packaging and manual
validation for this MVP target Zotero 9.0.4.

Manual Zotero 9.0.4 validation has confirmed real selection capture for item
key, attachment key, page, page label, selected text, and bbox. Annotation key
was `null` for that selection. DOM popup hosting from `Run JavaScript` returned
`popup_host_unavailable`, so prompt capture is the current MVP input path.
Unavailable navigation still leaves the local note visible instead of
inventing a jump target.

## Zotero 9.0.4 Manual Validation

K-K verified installation, startup, public API access, Zotero HTTP status
requests, and smoke-note upsert/cleanup in Zotero 9.0.4. K-L manual checking
has verified selection capture and identified the popup-host fallback need:

1. Zip the contents of `zotero-plugin/` with `manifest.json` at archive root
   and rename the archive to `.xpi`.
2. In Zotero 9.0.4, open `Tools` > `Plugins`, select the gear menu, and choose
   `Install Plugin From File...` for that `.xpi`.
3. Restart Zotero if requested and inspect the debug log for
   `[NOTEBOOK_AI Inspiration] startup`.
4. With a disposable test selection in a PDF open, run
   `captureSelectionWithPromptFallback()`, enter test note text/tags, and
   validate loopback receipt and local list output.
5. Continue to record unverified Reader UI/navigation capabilities explicitly;
   do not begin with an important real note.

## Later Phase Connection

- K-C: implement an explicitly approved backend ingestion endpoint and
  persistence policy; plugin remains loopback-only.
- K-D: preview note-to-document/chunk/page/bbox matching and unmatched
  diagnostics.
- K-E: link notes to reviewed objects and enforce the object-review gate
  before mechanism review.

Those phases may consume the raw payload; none may overwrite the user's raw
note fields.

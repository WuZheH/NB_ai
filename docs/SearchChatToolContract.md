# Search Chat Tool Contract

## Stable surface

Search exposes exactly eight tools:

| Tool | Mutation | Purpose |
|---|---:|---|
| `search` | no | compact ranked snippets |
| `fetch` | no | one selected full evidence fragment |
| `export_evidence` | no | evidence pack for selected fragment IDs |
| `list_library` | no | compact library summary and filters |
| `import_preview` | no library write | inspect one attached or Inbox PDF |
| `import_document` | yes | confirm and run the existing import pipeline |
| `delete_preview` | no | compact Candidate10 deletion impact |
| `delete_document` | destructive | confirmed permanent Search-data deletion |

All tools set `openWorldHint: false`. Read tools and previews set
`readOnlyHint: true`, `destructiveHint: false`, and `idempotentHint: true`.
`import_document` is a non-idempotent write. `delete_document` is a
non-idempotent destructive write.

## Compact outputs

`list_library` returns:

```json
{
  "document_id": 1,
  "title": "Example",
  "type": "paper",
  "imported_at": "2026-07-24",
  "chunk_count": 20,
  "has_pdf": true,
  "duplicate_status": "not_evaluated",
  "status": "active"
}
```

It never returns a path, username, schema detail, or vector internals.

`delete_preview` returns only:

```json
{
  "document_id": 1,
  "title": "Example",
  "safe_to_delete": true,
  "pdf_preserved": true,
  "notes_preserved": true,
  "blockers": [],
  "confirmation_token": "opaque"
}
```

The revision, Candidate10 preview token, impact hash, row counts, recovery
location, and audit detail stay inside Search. `delete_document` does not
return the internal audit ID.

`import_preview` returns title, PDF SHA256, duplicate status, estimated
pages/chunks, document type, warnings, and an opaque confirmation token. It
does not return PDF text or a local path.

## Confirmation boundary

Write tools require two independent protections:

1. ChatGPT uses accurate write/destructive annotations and asks the user.
2. Search requires a fresh one-time preview token and literal
   `confirmed: true` from the current conversation.

The model never asks the user to type a document ID, revision, impact hash, or
token. Vague intent is not confirmation. A token is consumed once and expires
after ten minutes.

## Errors

MCP success responses use each tool's declared output schema. Failures use the
standard tool error path with `isError: true` and compact text content; they do
not put an arbitrary error object into success `structuredContent`.

Stable failures cover backend unavailable, timeout, malformed backend data,
model unavailable, index unavailable, not found, invalid ID, stale
confirmation, blocked deletion, duplicate import, invalid PDF, and staging
failure. Tool errors never contain server stack traces, local paths, tokens, or
raw PDF/note content.

## Adapter separation

The Actions adapter publishes the same eight operation IDs and uses bearer
authentication. It is a fallback only. It does not duplicate Core logic and it
cannot be enabled in the same GPT as Apps.

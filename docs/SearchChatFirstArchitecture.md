# Search Chat-first Architecture

## Product direction

Search Core is the product. ChatGPT and ordinary chat are the daily user
surface. Search Desktop remains a Runtime Manager, diagnostics surface, and
fallback interface; its existing pages are retained but are no longer the
primary product roadmap.

```text
                         Search Core
                     (SQLite, FTS, vectors)
                              |
                   loopback Chat Tool API
                              |
                 Search MCP / Actions adapter
                       /              \
          Secure MCP Tunnel       authenticated HTTPS
                  |                    |
       ChatGPT / Codex App       Private GPT fallback
```

The adapters contain no database, import, deletion, embedding, or ranking
business logic. They validate compact tool input and call the Python Core.

## Runtime boundaries

- FastAPI and MCP bind only to loopback.
- The Desktop supervisor creates one random internal gateway token per Runtime
  session and passes it only to FastAPI and MCP child-process environments.
- The token is not persisted, returned by Runtime status, sent to the renderer,
  or logged.
- The supervisor explicitly gives both processes the same Search Import Inbox
  on the data-project drive. Model code does not derive an AppData fallback.
- Actions require a separate user-configured bearer token and an explicit
  HTTPS public base URL. These values are never generated into source code.
- Production data remains outside packages and is never exposed through the
  tunnel as a filesystem.

## Import architecture

`import_preview` accepts either:

1. an OpenAI Apps SDK file parameter; or
2. a PDF already placed in the Search Import Inbox.

For an Apps SDK file parameter, Node streams the temporary HTTPS download into
the explicit Inbox without putting PDF bytes into model content. It validates
HTTPS, declared/response MIME, 200 MB maximum size, `%PDF-` magic, and SHA256.
The staged filename is random and does not reuse the user filename. A confirmed
import calls the existing Search import pipeline; the temporary attachment copy
is then removed. A preview never writes the production library.

## Deletion architecture

The chat adapter wraps the Candidate10/Candidate11 deletion implementation:
preview token, revision/impact validation, recovery package, database
transaction, note/PDF/shared-object protection, FTS/vector cleanup, and audit
remain in one Python service. The model receives only the compact preview and
confirmation token. The permanent delete tool consumes the token once and
requires `confirmed: true`.

The 2026-07-24 production reconciliation was limited to documents 9 and 8.
Their committed database deletions had left 148 and 101 stale FTS rows after a
Windows file-replacement failure. The scoped in-place FTS cleanup completed
both audits without deleting another document. The post-reconciliation
production tree baseline is:

- 197 files
- 670,314,964 bytes
- `search.tree-hash.v1`
  `93A2612B06A74ED31504AA1A371CE766B5E8D9A9A5B977A61CD1FD9B639594EC`

## Desktop policy

Candidate12 does not redesign Desktop cards, covers, inspectors, or dialogs.
Existing Desktop code remains available. Future Desktop work should prioritize:

- Search running
- API Ready
- Retrieval Ready
- MCP Ready
- completely quit
- view logs

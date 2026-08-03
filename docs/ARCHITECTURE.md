# Architecture

NOTEBOOK_AI is a local-first FastAPI and React application. The backend owns
data access, import workflows, retrieval, review state, and Zotero integration;
the frontend composes those capabilities into research-library and workspace
features.

## Backend

- `app/main.py` creates the FastAPI application and registers routers.
- `app/core/` contains shared configuration, canonical paths, SQLite connection
  factories, base exceptions, and logger creation.
- `app/api/` contains HTTP adapters. The library API is composed in
  `app/api/library/`; `app/api/library_api.py` remains the stable import façade.
- `app/services/` retains public service import paths used by routers, scripts,
  tests, and integrations.
- `app/domains/` groups larger chapter-review, database-search, and library
  responsibilities behind those stable service façades.
- `app/domains/retrieval/` composes the existing high-quality PDF service with
  a separate derived Zotero user-note vector index. It owns source-aware
  fragment fields and evidence rendering; it does not contain FTS ranking or
  duplicate the embedding/reranker implementations.
- `app/cli.py` preserves `app.cli:app`; command-group surfaces live under
  `app/cli_commands/` and the compatibility runtime remains in
  `app/cli_runtime.py`.

SQLite factories distinguish read/write, read-only, and immutable connections.
Callers continue to choose transaction, timeout, row-factory, URI, foreign-key,
and temporary-store behavior explicitly.

## Frontend

- `frontend/src/main.jsx` mounts the application.
- `frontend/src/app/` owns application composition, route parsing/building, and
  navigation state without introducing a router framework.
- `frontend/src/features/` exposes retrieval, search, library, importing,
  workspace, object, and mechanism feature surfaces.
- `frontend/src/shared/` owns the JSON API client, request-state primitives,
  shared UI state messages, and semantically common display helpers.
- Historical page and component paths remain available where other modules may
  still import them.
- Workspace styles are loaded through `frontend/src/styles/workspace.css` in
  their original cascade order and implemented in feature-owned style chunks.

## Scripts and integrations

Operational scripts are grouped under `scripts/runtime`, `importing`, `index`,
`maintenance`, `migrations`, and `zotero`. Historical top-level script paths are
thin compatibility launchers. Zotero extension source and packaging contracts
live under `zotero-plugin/`. The ChatGPT Developer Mode MCP App is isolated in
`integrations/notebook_ai_chatgpt_app/`; its TypeScript server is a read-only
HTTP adapter to FastAPI and its React widget never accesses SQLite directly.

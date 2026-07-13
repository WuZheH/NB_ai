# Running NOTEBOOK_AI

## Backend

Use the existing NOTEBOOK_AI conda environment without changing the global PATH:

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Frontend

`frontend/node_modules` and `frontend/dist` are rebuildable local artifacts and are
not part of the source tree.

```powershell
Set-Location frontend
npm ci
npm run dev
```

Dependency installation is a manual user action. The lockfile is retained.

## Retrieval index

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/status_retrieval_fts.py
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/search_retrieval_fts.py "spectral clustering"
```

Only run build or sync commands when an index refresh is intended:

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/build_retrieval_fts.py
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/sync_vector_store.py
```

The separate Zotero user-note vector index has explicit build, incremental
sync, and read-only status commands:

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/index/build_zotero_note_vectors.py
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/index/sync_zotero_note_vectors.py
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/index/status_zotero_note_vectors.py
```

## ChatGPT Developer Mode App

The read-only MCP App and its complete local build, security, Inspector,
HTTPS-tunnel, and ChatGPT connection instructions live in
`integrations/notebook_ai_chatgpt_app/README.md`. The server binds to loopback
and refuses unauthenticated startup unless the documented development switch
is explicitly set.

## Zotero plugin

The plugin source is under `zotero-plugin/`. Package it with:

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" scripts/package_zotero_inspiration_plugin.py --json
```

Packaging output is rebuildable and is not retained in the source tree.

## Script layout

Stable commands remain available at their historical `scripts/<name>` paths.
Their implementations are grouped by purpose under:

- `scripts/runtime/`
- `scripts/importing/`
- `scripts/index/`
- `scripts/maintenance/`
- `scripts/migrations/`
- `scripts/zotero/`

Use the historical command paths in automation; the thin wrappers preserve
arguments, standard output, and exit codes.

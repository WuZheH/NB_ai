# Development

## Backend checks

Use a project-compatible Python environment without changing the global PATH:

```powershell
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" -m pytest tests/core
& "<PATH_TO_NOTEBOOK_AI_PYTHON>" -c "import app.main; print(len(app.main.app.routes))"
```

The core suite checks application import, route contracts, compatibility import
paths, CLI commands, database read-only behavior and integrity, retrieval model
contracts, FTS independence/status, frontend source contracts, and the Zotero
plugin surface. Tests must not rebuild indexes or write the production database.

## Frontend checks

Relative imports and public route/API contracts are covered by `tests/core` and
do not require `node_modules`. When dependencies have been restored from the
lockfile, run the normal Vite build:

```powershell
Set-Location frontend
npm ci
npm run build
```

Do not commit `node_modules`, `dist`, local caches, derived indexes, or model
weights.

## Compatibility surfaces

Keep existing FastAPI paths/methods and response models stable. Preserve
`app.api.library_api`, public service modules under `app.services`,
`app.cli:app`, top-level script launch paths, and legacy frontend entry paths
while implementations are organized into domain and feature modules.

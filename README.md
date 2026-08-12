# READ — Local Search Core (public distribution)

A local-first document knowledge base with full-text and semantic retrieval,
ChatGPT/MCP integration, and a desktop shell. This repository is the public
source distribution of the READ product.

## What is included

- `app/` — Python core: library, retrieval generations, FTS, vector stores,
  chat tool API, MCP adapter backend.
- `frontend/` — Vite web UI.
- `integrations/` — ChatGPT/Codex MCP adapter (`notebook_ai_chatgpt_app`)
  and the Electron desktop shell (`search_desktop`).
- `scripts/` — bootstrap, build, test, schema migrations and index
  maintenance entry points.
- `tests/` — core test suite (no private fixtures; all synthetic).
- `config/` — example environment and local-path templates.
- `zotero-plugin/` — optional Zotero inspiration plugin source.
- `packages/` — shared frontend design tokens.

## Requirements

- Python 3.11 (conda recommended) with `requirements.txt` installed.
- Node.js 18+ for the frontend, MCP adapter, and desktop shell.

## Quick start

```powershell
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install and build the frontend
npm --prefix .\frontend install
npm --prefix .\frontend run build

# 3. Install and build the MCP adapter
npm --prefix .\integrations\notebook_ai_chatgpt_app install
npm --prefix .\integrations\notebook_ai_chatgpt_app run build

# 4. Set an empty data directory and start the API
$env:SEARCH_DATA_DIR = "$env:LOCALAPPDATA\Search\data"
python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On first start the application creates the database schema and an empty
retrieval baseline automatically. No pre-existing database or index is
required.

## Configuration

- Copy `config/environment.example.txt` to your runtime config and edit it.
- Copy `config/local_paths.example.json` to `config/local_paths.json` and set
  your own data/model paths. No personal paths are required.
- Machine config (optional but recommended for semantic search): create
  `%APPDATA%\Search\machine-config.json` with the schema

  ```json
  {
    "schema_version": 1,
    "high_quality_search": {
      "embedding_model_path": "C:\\path\\to\\Qwen3-Embedding-0.6B",
      "reranker_model_path": "C:\\path\\to\\Qwen3-Reranker-0.6B"
    }
  }
  ```

  and set `SEARCH_MACHINE_CONFIG_PATH` to that file. Without it, imports
  still complete and search falls back to lexical retrieval.
- Zotero integration is optional: provide a `data/zotero/zotero_source_config.json`
  (see `config/local_paths.example.json` for the shape). Without it, Zotero
  scoped tools return a clear configuration error and everything else works.

## Start the backend and MCP adapter

```powershell
# Backend (from the repository root)
$env:SEARCH_DATA_DIR = "$env:LOCALAPPDATA\Search\data"
python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Chat gateway token (>= 32 chars) shared by backend and MCP client
$env:SEARCH_CHAT_GATEWAY_TOKEN = "<your-random-token>"

# MCP adapter server (separate terminal)
npm --prefix .\integrations\notebook_ai_chatgpt_app start
```

## Connect READ to ChatGPT

Register the MCP adapter in your ChatGPT/Codex MCP configuration with the
adapter's stdio command, e.g.:

```json
{
  "mcpServers": {
    "read": {
      "command": "node",
      "args": [
        "integrations/notebook_ai_chatgpt_app/dist/server/index.js"
      ],
      "env": {
        "SEARCH_BACKEND_URL": "http://127.0.0.1:8000",
        "SEARCH_BACKEND_BEARER_TOKEN": "<your-random-token>"
      }
    }
  }
}
```

`SEARCH_BACKEND_BEARER_TOKEN` must equal the backend's
`SEARCH_CHAT_GATEWAY_TOKEN`. The adapter sends it as a bearer token and
identifies itself with the `X-Search-Chat-Adapter: mcp` header.

The adapter exposes 10 tools: `search`, `fetch`, `export_evidence`,
`list_library`, `integrity_report`, `import_preview`, `import_document`,
`import_status`, `delete_preview`, `delete_document`.

## Import your first PDF

1. Place the PDF in the import inbox. With
   `SEARCH_DATA_DIR = %LOCALAPPDATA%\Search\data`, the inbox is
   `%LOCALAPPDATA%\search-import-inbox` (or set `SEARCH_IMPORT_INBOX`).
2. In the ChatGPT conversation, ask READ to import the file. The assistant
   calls `import_preview` first, then `import_document` exactly once after
   your explicit confirmation.
3. Check the result with `import_status` or `list_library`.

All imported content stays local: the database, indexes, vector store, and
imported PDF copies live under your `SEARCH_DATA_DIR` only.

## Data location

- Database: `$SEARCH_DATA_DIR\db\research_memory.db`
- Full-text index: `$SEARCH_DATA_DIR\search_index\`
- Vector store: `$SEARCH_DATA_DIR\vector_store\`
- Imported PDFs: `$SEARCH_DATA_DIR\pdfs\`
- Import operation journals: `$SEARCH_DATA_DIR\import_operation_journal\`
- Zotero snapshots (optional): `$SEARCH_DATA_DIR\zotero\`

## Backing up personal data

Stop the backend, then copy the whole `SEARCH_DATA_DIR` directory to your
backup location. The machine config (`%APPDATA%\Search\machine-config.json`)
contains only model paths and can be recreated by hand.

## Uninstalling / removing local data

Stop the backend and MCP adapter, then delete:

- `$SEARCH_DATA_DIR` (all imported documents, indexes, and databases)
- `%APPDATA%\Search\machine-config.json`
- `%LOCALAPPDATA%\search-import-inbox\` (staged PDFs)
- the repository directory (or `npm uninstall -g` if installed globally)

## Tests

```powershell
python -B -m pytest -q .\tests\core -p no:cacheprovider
npm --prefix .\integrations\notebook_ai_chatgpt_app test
npm --prefix .\integrations\search_desktop test
node --test .\frontend\tests\*.test.mjs
```

## License

MIT — see [LICENSE](LICENSE). This distribution contains no private user
data, no real Zotero snapshots, and no personal configuration.

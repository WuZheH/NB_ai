# NOTEBOOK_AI

NOTEBOOK_AI is a local-first research evidence retrieval and export system for PDF text, Zotero annotations, and local notes.

## Retrieval modes

- High-quality search: `Qwen3-Embedding-0.6B` semantic recall followed by `Qwen3-Reranker-0.6B` reranking.
- Keyword search: independent local FTS5/BM25 precision and coverage modes.
- Unified evidence: stable `RetrievalFragment` identities across PDF chunks, highlights, comments, Zotero notes, personal notes, and Markdown notes.
- Evidence Basket: server-side Markdown, JSONL, and JSON export with provenance and stable IDs.
- ChatGPT Developer Mode App: read-only MCP `search`, `fetch`, and
  `export_evidence` tools with an embedded React evidence widget.

FTS does not silently replace high-quality model search. Model or vector runtime problems must be reported explicitly.

## Local-only assets

This repository directory intentionally keeps local runtime data. The application expects the user to maintain:

- production SQLite data;
- Zotero snapshots and mappings;
- source PDFs and converted Markdown;
- local Qwen model files;
- FTS and vector indexes.

These assets are not downloaded automatically and must not be uploaded without a separate privacy and copyright review.

## Run

See [docs/RUNNING.md](docs/RUNNING.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SEARCH_PIPELINE.md](docs/SEARCH_PIPELINE.md),
[docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md), and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Main components

- `app/`: FastAPI backend and retrieval services.
- `frontend/src/`: React/Vite user interface.
- `zotero-plugin/`: Zotero Reader and Library integration.
- `scripts/`: runtime, import, index, maintenance, migration, and packaging commands.
- `integrations/notebook_ai_chatgpt_app/`: Apps SDK/MCP server, React widget,
  local smoke tests, and ChatGPT connection instructions.
- `data/`: production data, local source documents, exports, and indexes.
- `config/`: deterministic local retrieval configuration and path templates.

## Safety

The application is designed for localhost use. It does not require OpenAI or another external LLM API for retrieval. Back up production data before running migrations or write-enabled maintenance commands.

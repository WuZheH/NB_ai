# Local Data Layout

The application uses these runtime locations:

- `data/db/`: production SQLite database and backups.
- `data/zotero/`: Zotero snapshot and source mappings.
- `data/pdfs/`: source PDF files.
- `data/notes/`: local user notes.
- `data/converted_md/`: converted source text.
- `data/search_index/`: derived FTS index.
- `data/vector_index/`: legacy vector index.
- `data/vector_store/`: current LanceDB vector store.
- `data/lancedb/`: optional LanceDB location when present.
- `data/exports/`: retained user evidence and Zotero Markdown exports.

Indexes are derived runtime assets and can be rebuilt only by an explicit index
maintenance command. Production data is never migrated merely by starting the
application. Verify backups before running write-enabled scripts or migrations.

Active model paths are configured outside this project and should point to the local `Qwen3-Embedding-0.6B` and `Qwen3-Reranker-0.6B` directories.

Before publishing a repository copy, review the production database, Zotero
snapshot, notes, PDFs, and converted Markdown for privacy and redistribution
rights. Derived search/vector indexes and model weights should remain outside
source control.

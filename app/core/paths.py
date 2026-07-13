from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_DIR = DATA_DIR / "db"
DEFAULT_DB_PATH = DB_DIR / "research_memory.db"

# Canonical product-data locations.  Keep these values derived from the
# workspace root so moving the repository does not introduce another
# machine-specific path.  Existing names remain available below as aliases.
PRODUCTION_DB_PATH = DEFAULT_DB_PATH
ZOTERO_DIR = DATA_DIR / "zotero"
ZOTERO_SNAPSHOT_DIR = ZOTERO_DIR / "snapshot"
ZOTERO_SNAPSHOT_PATH = ZOTERO_SNAPSHOT_DIR / "zotero.sqlite"
PDFS_DIR = DATA_DIR / "pdfs"
NOTES_DIR = DATA_DIR / "notes"
CONVERTED_MD_DIR = DATA_DIR / "converted_md"

FTS_INDEX_DIR = DATA_DIR / "search_index"
FTS_DB_PATH = FTS_INDEX_DIR / "retrieval_fts_v1.db"
FTS_MANIFEST_PATH = FTS_INDEX_DIR / "retrieval_fts_v1_manifest.json"

VECTOR_INDEX_DIR = DATA_DIR / "vector_index"
VECTOR_STORE_DIR = DATA_DIR / "vector_store"
LANCEDB_DIR = VECTOR_STORE_DIR / "lancedb"
ZOTERO_NOTE_VECTOR_DIR = VECTOR_STORE_DIR / "zotero_user_notes_v1"

# The checked-in project sits next to the shared model_cache directory.  The
# model services retain their existing environment-variable resolution and
# defaults; these constants centralize the same default locations only.
MODEL_CACHE_ROOT = PROJECT_ROOT.parent / "model_cache"
EMBEDDING_MODEL_PATH = MODEL_CACHE_ROOT / "Qwen3-Embedding-0.6B"
RERANKER_MODEL_PATH = MODEL_CACHE_ROOT / "Qwen3-Reranker-0.6B"


def ensure_db_dir() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)

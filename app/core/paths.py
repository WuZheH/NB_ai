from __future__ import annotations

import os
from pathlib import Path

RUNTIME_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_configured_path(value: str, *, label: str) -> Path:
    cleaned = str(value or "").strip()
    if not cleaned or "\x00" in cleaned:
        raise RuntimeError(f"{label} is invalid")
    candidate = Path(cleaned).expanduser()
    if not candidate.is_absolute():
        candidate = RUNTIME_PROJECT_ROOT / candidate
    return candidate.resolve()


def _resolve_data_dir() -> Path:
    configured = os.environ.get("SEARCH_DATA_DIR", "").strip()
    if configured:
        return _resolve_configured_path(configured, label="SEARCH_DATA_DIR")

    legacy_root = os.environ.get("NOTEBOOK_AI_DATA_PROJECT_ROOT", "").strip()
    if legacy_root:
        return _resolve_configured_path(
            legacy_root,
            label="NOTEBOOK_AI_DATA_PROJECT_ROOT",
        ) / "data"
    return RUNTIME_PROJECT_ROOT / "data"


# Python code may live inside the packaged Electron application while user
# data remains outside that immutable runtime. SEARCH_DATA_DIR names the data
# directory directly; the NOTEBOOK_AI_* root remains a compatibility alias.
DATA_DIR = _resolve_data_dir()
DATA_PROJECT_ROOT = DATA_DIR.parent
# PROJECT_ROOT is the immutable code/runtime root.  Data may be relocated via
# SEARCH_DATA_DIR, so data-relative compatibility paths must use
# DATA_PROJECT_ROOT explicitly instead of changing where code/resources live.
PROJECT_ROOT = RUNTIME_PROJECT_ROOT
DB_DIR = DATA_DIR / "db"
DEFAULT_DB_PATH = DB_DIR / "research_memory.db"
OUTPUTS_DIR = DATA_PROJECT_ROOT / "outputs"


def _resolve_runtime_state_dir() -> Path:
    configured = os.environ.get("SEARCH_RUNTIME_DIR", "").strip()
    if configured:
        return _resolve_configured_path(configured, label="SEARCH_RUNTIME_DIR")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return _resolve_configured_path(
            str(Path(local_app_data) / "Search"),
            label="LOCALAPPDATA",
        )
    return RUNTIME_PROJECT_ROOT / ".codex_tmp" / "runtime"


RUNTIME_STATE_DIR = _resolve_runtime_state_dir()

# Canonical product-data locations.  Keep these values derived from the
# workspace root so moving the repository does not introduce another
# machine-specific path.  Existing names remain available below as aliases.
PRODUCTION_DB_PATH = DEFAULT_DB_PATH
ZOTERO_DIR = DATA_DIR / "zotero"
ZOTERO_SNAPSHOT_DIR = ZOTERO_DIR / "snapshot"
ZOTERO_SNAPSHOT_PATH = ZOTERO_SNAPSHOT_DIR / "zotero.sqlite"
ZOTERO_LIBRARY_DIR = _resolve_configured_path(
    os.environ.get("SEARCH_ZOTERO_DATA_DIR") or str(ZOTERO_DIR / "library"),
    label="SEARCH_ZOTERO_DATA_DIR",
)
ZOTERO_LIBRARY_DB_PATH = ZOTERO_LIBRARY_DIR / "zotero.sqlite"
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

# Optional local models are never downloaded automatically.  An unconfigured
# checkout looks beneath its ignored data directory and reports missing models
# through the existing service diagnostics.
MODEL_CACHE_ROOT = _resolve_configured_path(
    os.environ.get("SEARCH_MODEL_CACHE_DIR")
    or os.environ.get("NOTEBOOK_AI_MODEL_CACHE_ROOT")
    or str(DATA_DIR / "models"),
    label="SEARCH_MODEL_CACHE_DIR",
)
EMBEDDING_MODEL_PATH = _resolve_configured_path(
    os.environ.get("SEARCH_EMBEDDING_MODEL")
    or os.environ.get("NOTEBOOK_AI_EMBEDDING_MODEL_PATH")
    or str(MODEL_CACHE_ROOT / "Qwen3-Embedding-0.6B"),
    label="SEARCH_EMBEDDING_MODEL",
)
RERANKER_MODEL_PATH = _resolve_configured_path(
    os.environ.get("SEARCH_RERANKER_MODEL")
    or os.environ.get("NOTEBOOK_AI_RERANKER_MODEL_PATH")
    or str(MODEL_CACHE_ROOT / "Qwen3-Reranker-0.6B"),
    label="SEARCH_RERANKER_MODEL",
)


def ensure_db_dir() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)

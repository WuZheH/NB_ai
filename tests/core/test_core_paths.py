from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from app.core import paths
from app.services import local_embedding_service, local_reranker_service


def test_product_paths_are_derived_from_the_project_root() -> None:
    assert paths.DATA_DIR == paths.PROJECT_ROOT / "data"
    assert paths.PRODUCTION_DB_PATH == paths.DATA_DIR / "db" / "research_memory.db"
    assert paths.DEFAULT_DB_PATH == paths.PRODUCTION_DB_PATH
    assert paths.ZOTERO_SNAPSHOT_PATH == paths.DATA_DIR / "zotero" / "snapshot" / "zotero.sqlite"
    assert paths.PDFS_DIR == paths.DATA_DIR / "pdfs"
    assert paths.NOTES_DIR == paths.DATA_DIR / "notes"
    assert paths.CONVERTED_MD_DIR == paths.DATA_DIR / "converted_md"
    assert paths.FTS_DB_PATH == paths.DATA_DIR / "search_index" / "retrieval_fts_v1.db"
    assert paths.FTS_MANIFEST_PATH == (
        paths.DATA_DIR / "search_index" / "retrieval_fts_v1_manifest.json"
    )
    assert paths.VECTOR_INDEX_DIR == paths.DATA_DIR / "vector_index"
    assert paths.VECTOR_STORE_DIR == paths.DATA_DIR / "vector_store"
    assert paths.LANCEDB_DIR == paths.VECTOR_STORE_DIR / "lancedb"


def test_canonical_model_defaults_match_the_legacy_search_contract() -> None:
    assert paths.EMBEDDING_MODEL_PATH == local_embedding_service.DEFAULT_MODEL_PATH
    assert paths.RERANKER_MODEL_PATH == local_reranker_service.DEFAULT_RERANKER_MODEL_PATH
    assert paths.EMBEDDING_MODEL_PATH.name == "Qwen3-Embedding-0.6B"
    assert paths.RERANKER_MODEL_PATH.name == "Qwen3-Reranker-0.6B"


def test_packaged_code_root_and_stable_data_root_are_independent(tmp_path: Path) -> None:
    data_project_root = tmp_path / "stable-data-project"
    data_project_root.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "NOTEBOOK_AI_DATA_PROJECT_ROOT": str(data_project_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json; from app.core.paths import PROJECT_ROOT, RUNTIME_PROJECT_ROOT; "
                "print(json.dumps({'data': str(PROJECT_ROOT), 'runtime': str(RUNTIME_PROJECT_ROOT)}))"
            ),
        ],
        cwd=str(paths.RUNTIME_PROJECT_ROOT),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(completed.stdout)
    assert Path(value["data"]) == data_project_root.resolve()
    assert Path(value["runtime"]) == paths.RUNTIME_PROJECT_ROOT
    assert value["data"] != value["runtime"]

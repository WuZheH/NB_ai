from __future__ import annotations

"""Isolated production-shaped data sandbox for the core test suite.

The full test suite must run without the real production ``data`` tree.
Every test that exercises product read paths (generation guard, FTS status,
search pushdown, chat tool gateway) resolves against this sandbox instead of
the repository data directory.  The sandbox is a real, valid legacy
retrieval environment: full-schema SQLite database, built FTS index with a
manifest whose ``production_db_sha256`` matches the database, and the legacy
vector/native-note directories.

``SEARCH_DATA_DIR`` is set here before any ``app`` import so that
``app.core.paths`` and every module-level path constant resolve inside the
sandbox.  Tests that need their own data use explicit ``tmp_path`` runtimes
and are unaffected.

Safety: ``SEARCH_TEST_DATA_ROOT`` (when set) is only a *parent* directory.
The sandbox is a uniquely named child guarded by an ownership sentinel; only
owned children of previous runs are ever removed, never the parent.
"""

import os

from tests.core.sandbox_support import owned_sandbox_root, purge_owned_sandboxes

# Remove only sandbox children owned by previous runs; the parent directory
# itself is never touched.
purge_owned_sandboxes()

_SANDBOX_ROOT = owned_sandbox_root()


def _build_sandbox() -> str:
    os.environ["SEARCH_DATA_DIR"] = str(_SANDBOX_ROOT)

    from app.core import paths
    from app.db.session import Base

    import app.models  # noqa: F401 - registers all model tables

    from sqlalchemy import create_engine

    paths.ensure_db_dir()
    database = paths.DEFAULT_DB_PATH
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        now = "2026-08-10T00:00:00+00:00"
        connection.exec_driver_sql(
            "INSERT INTO documents (id, title, document_type, content_layer, "
            "read_status, created_at, updated_at, object_import_mode) "
            "VALUES (1, 'Sandbox Book 1', 'book', 'source', 'read', ?, ?, "
            "'chaptered')",
            (now, now),
        )
        connection.exec_driver_sql(
            "INSERT INTO documents (id, title, document_type, content_layer, "
            "read_status, created_at, updated_at, object_import_mode) "
            "VALUES (2, 'Sandbox Book 2', 'book', 'source', 'read', ?, ?, "
            "'chaptered')",
            (now, now),
        )
        connection.exec_driver_sql(
            "INSERT INTO knowledge_chunks (id, document_id, chunk_index, "
            "heading_path, chunk_text, content_hash, pdf_page_start, "
            "pdf_page_end, created_at, updated_at) "
            "VALUES (101, 1, 0, 'H', 'sandbox chunk one', 'sandbox-hash-1', "
            "1, 1, ?, ?)",
            (now, now),
        )
        connection.exec_driver_sql(
            "INSERT INTO knowledge_chunks (id, document_id, chunk_index, "
            "heading_path, chunk_text, content_hash, pdf_page_start, "
            "pdf_page_end, created_at, updated_at) "
            "VALUES (102, 2, 0, 'H', 'sandbox chunk two', 'sandbox-hash-2', "
            "1, 1, ?, ?)",
            (now, now),
        )
    engine.dispose()

    from app.services.retrieval.fts_index_service import build_retrieval_fts

    build_retrieval_fts()

    paths.VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    paths.LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    paths.ZOTERO_NOTE_VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    (paths.VECTOR_STORE_DIR / "vector_manifest.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    return str(_SANDBOX_ROOT)


SANDBOX = _build_sandbox()

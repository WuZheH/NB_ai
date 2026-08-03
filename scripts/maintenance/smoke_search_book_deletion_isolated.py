from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


FIXTURE_DOCUMENT_ID = 910001
FIXTURE_TITLE = "Candidate10 Isolated Smoke Book"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed or verify the packaged Search book-deletion isolated smoke fixture."
    )
    parser.add_argument("--action", choices=("seed", "verify"), required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--allow-isolated-smoke", action="store_true")
    args = parser.parse_args()
    if not args.allow_isolated_smoke:
        raise RuntimeError("isolated_smoke_confirmation_required")

    data_dir = args.data_dir.resolve()
    archive_root = args.archive_root.resolve()
    if archive_root == data_dir or archive_root.is_relative_to(data_dir):
        raise RuntimeError("isolated_smoke_archive_must_be_outside_data")
    db_path = data_dir / "db" / "research_memory.db"
    if not db_path.is_file():
        raise RuntimeError("isolated_smoke_database_missing")

    if args.action == "seed":
        result = seed_fixture(db_path=db_path, data_dir=data_dir)
    else:
        result = verify_fixture(
            db_path=db_path,
            archive_root=archive_root,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def seed_fixture(*, db_path: Path, data_dir: Path) -> dict[str, object]:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        document_count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        if document_count:
            raise RuntimeError("isolated_smoke_database_not_empty")
        connection.execute(
            """
            INSERT INTO documents (
                id, title, document_type, content_layer, source_path, pdf_path,
                zotero_key, read_status, research_direction,
                object_import_mode, object_import_status, created_at, updated_at
            ) VALUES (?, ?, 'book', 'source', NULL, NULL, NULL, 'read', NULL,
                      NULL, NULL, '2026-07-24T00:00:00', '2026-07-24T00:00:00')
            """,
            (FIXTURE_DOCUMENT_ID, FIXTURE_TITLE),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    _create_empty_passage_vector_table(data_dir)
    return {
        "status": "seeded",
        "document_id": FIXTURE_DOCUMENT_ID,
        "title": FIXTURE_TITLE,
    }


def verify_fixture(*, db_path: Path, archive_root: Path) -> dict[str, object]:
    uri = db_path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        document_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE id = ?",
                (FIXTURE_DOCUMENT_ID,),
            ).fetchone()[0]
        )
        archive_state_count = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM library_archive_states WHERE document_id = ?",
                    (FIXTURE_DOCUMENT_ID,),
                ).fetchone()[0]
            )
            if _table_exists(connection, "library_archive_states")
            else 0
        )
        foreign_key_issues = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        connection.close()
    if document_count or archive_state_count or foreign_key_issues:
        raise RuntimeError("isolated_smoke_database_cleanup_failed")

    packages = sorted(
        path
        for path in archive_root.glob("delete-*")
        if path.is_dir()
    )
    if len(packages) != 1:
        raise RuntimeError("isolated_smoke_recovery_package_count_invalid")
    report_path = packages[0] / "deletion_report.json"
    manifest_path = packages[0] / "recovery_manifest.json"
    rows_path = packages[0] / "database_rows.json"
    if not all(path.is_file() for path in (report_path, manifest_path, rows_path)):
        raise RuntimeError("isolated_smoke_recovery_package_incomplete")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("result") != "completed"
        or int(report.get("document_id") or 0) != FIXTURE_DOCUMENT_ID
    ):
        raise RuntimeError("isolated_smoke_deletion_report_invalid")
    return {
        "status": "verified",
        "document_id": FIXTURE_DOCUMENT_ID,
        "foreign_key_issue_count": foreign_key_issues,
        "recovery_package_count": len(packages),
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _create_empty_passage_vector_table(data_dir: Path) -> None:
    os.environ["SEARCH_DATA_DIR"] = str(data_dir)
    runtime_project_root = Path(__file__).resolve().parents[2]
    if str(runtime_project_root) not in sys.path:
        sys.path.insert(0, str(runtime_project_root))
    from app.services import vector_store_service

    document = SimpleNamespace(
        id=0,
        title="Isolated Vector Schema",
        document_type="book",
        object_import_mode=None,
    )
    chunk = SimpleNamespace(
        id=0,
        document_id=0,
        chunk_text="",
        heading_path="",
        content_hash="isolated-vector-schema",
        updated_at=None,
        pdf_page_start=None,
        pdf_page_end=None,
        chapter_id=None,
    )
    record = vector_store_service.build_passage_schema_record(document, chunk)
    database = vector_store_service.open_vector_store(
        data_dir / "vector_store" / "lancedb"
    )
    table = database.create_table(
        vector_store_service.PASSAGE_TABLE,
        data=[record],
        mode="create",
    )
    table.delete("vector_id = 'chunk:0:0'")


if __name__ == "__main__":
    raise SystemExit(main())

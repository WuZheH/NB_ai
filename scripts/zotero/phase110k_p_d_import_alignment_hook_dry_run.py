from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from scripts.phase110k_p_c_import_time_alignment_batch_dry_run import (
    build_batch_dry_run_report,
)


MODE = "phase110k_p_d_import_alignment_hook_dry_run_v1"


def build_import_alignment_hook_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    document_id: int | None = None,
    attachment_key: str | None = None,
    zotero_item_key: str | None = None,
    source_path: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db = Path(db_path)
    input_payload = {
        "document_id": document_id,
        "attachment_key": attachment_key,
        "zotero_item_key": zotero_item_key,
        "source_path": source_path,
        "limit": limit,
        "plan_only": True,
    }
    blockers: list[str] = []
    resolved_document: dict[str, Any] | None = None
    resolved_attachment: dict[str, Any] | None = None

    if document_id is None and not attachment_key:
        blockers.append("document_id_or_attachment_key_required")
    else:
        with sqlite3.connect(_sqlite_uri(db), uri=True) as connection:
            connection.row_factory = sqlite3.Row
            if document_id is not None:
                resolved_document = _resolve_document(connection, document_id)
                if resolved_document is None:
                    blockers.append("document_not_found")
            resolved_attachment = _resolve_attachment(
                connection,
                attachment_key=attachment_key,
                document_id=document_id,
                zotero_item_key=zotero_item_key,
            )

    if blockers:
        return _base_report(
            db=db,
            input_payload=input_payload,
            status="blocked",
            resolved_document=resolved_document,
            resolved_attachment=resolved_attachment,
            batch_result=None,
            blockers=blockers,
            next_action="fix_import_hook_inputs",
        )

    batch_attachment_key = attachment_key or (
        resolved_attachment or {}
    ).get("zotero_attachment_key")
    batch_result = build_batch_dry_run_report(
        db,
        attachment_key=str(batch_attachment_key) if batch_attachment_key else None,
        document_id=document_id,
        limit=limit,
    )
    return _base_report(
        db=db,
        input_payload=input_payload,
        status="OK",
        resolved_document=resolved_document,
        resolved_attachment=resolved_attachment,
        batch_result=batch_result,
        blockers=[],
        next_action=_next_action(batch_result),
    )


def _base_report(
    *,
    db: Path,
    input_payload: dict[str, Any],
    status: str,
    resolved_document: dict[str, Any] | None,
    resolved_attachment: dict[str, Any] | None,
    batch_result: Mapping[str, Any] | None,
    blockers: list[str],
    next_action: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": MODE,
        "db_path": str(db),
        "input": input_payload,
        "resolved_document": resolved_document,
        "resolved_attachment": resolved_attachment,
        "batch_result": batch_result,
        "blockers": blockers,
        "hook_recommended_next_action": next_action,
        "db_write_performed": False,
        "import_performed": False,
        "marker_or_ocr_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "matched_fields_write_performed": False,
        "mechanism_draft_candidates_write_performed": False,
    }


def _resolve_document(
    connection: sqlite3.Connection,
    document_id: int,
) -> dict[str, Any] | None:
    if not _table_exists(connection, "documents"):
        return None
    columns = set(_columns(connection, "documents"))
    selected = [
        "id AS document_id",
        _select_expr(columns, "title"),
        _select_expr(columns, "source_path"),
        _select_expr(columns, "pdf_path"),
        _select_expr(columns, "zotero_key"),
    ]
    row = connection.execute(
        f"SELECT {', '.join(selected)} FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    chunk_count = 0
    if _table_exists(connection, "knowledge_chunks"):
        chunk_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0]
        )
    result = dict(row)
    result["chunk_count"] = chunk_count
    return result


def _resolve_attachment(
    connection: sqlite3.Connection,
    *,
    attachment_key: str | None,
    document_id: int | None,
    zotero_item_key: str | None,
) -> dict[str, Any] | None:
    source_row = _document_source_row(
        connection,
        attachment_key=attachment_key,
        document_id=document_id,
    )
    resolved_key = attachment_key or (
        source_row.get("zotero_attachment_key") if source_row else None
    )
    if not resolved_key:
        return None
    pdf_source = _zotero_pdf_source_row(connection, str(resolved_key))
    return {
        "zotero_attachment_key": str(resolved_key),
        "zotero_item_key": zotero_item_key
        or (source_row or {}).get("zotero_item_key")
        or (pdf_source or {}).get("zotero_item_key"),
        "document_source": source_row,
        "zotero_pdf_source": pdf_source,
    }


def _document_source_row(
    connection: sqlite3.Connection,
    *,
    attachment_key: str | None,
    document_id: int | None,
) -> dict[str, Any] | None:
    if not _table_exists(connection, "document_sources"):
        return None
    columns = set(_columns(connection, "document_sources"))
    selected = [
        _select_expr(columns, "document_id"),
        _select_expr(columns, "source_type"),
        _select_expr(columns, "zotero_attachment_key"),
        _select_expr(columns, "zotero_item_key"),
        _select_expr(columns, "zotero_source_id"),
        _select_expr(columns, "zotero_open_pdf_uri"),
    ]
    where = ["1 = 1"]
    params: list[Any] = []
    if attachment_key:
        where.append("zotero_attachment_key = ?")
        params.append(attachment_key)
    if document_id is not None:
        where.append("document_id = ?")
        params.append(document_id)
    row = connection.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM document_sources
        WHERE {' AND '.join(where)}
        ORDER BY rowid
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def _zotero_pdf_source_row(
    connection: sqlite3.Connection,
    attachment_key: str,
) -> dict[str, Any] | None:
    if not _table_exists(connection, "zotero_pdf_sources"):
        return None
    columns = set(_columns(connection, "zotero_pdf_sources"))
    selected = [
        _select_expr(columns, "id"),
        _select_expr(columns, "zotero_attachment_key"),
        _select_expr(columns, "zotero_item_key"),
        _select_expr(columns, "title"),
        _select_expr(columns, "resolved_pdf_path"),
        _select_expr(columns, "zotero_open_pdf_uri"),
    ]
    row = connection.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM zotero_pdf_sources
        WHERE zotero_attachment_key = ?
        ORDER BY rowid
        LIMIT 1
        """,
        (attachment_key,),
    ).fetchone()
    return dict(row) if row else None


def _next_action(batch_result: Mapping[str, Any]) -> str:
    summary = batch_result.get("summary") or {}
    if int(summary.get("total_notes_seen") or 0) == 0:
        return "no_zotero_notes_to_align"
    if int(summary.get("planned_count") or 0) > 0:
        return "review_batch_plan_before_apply"
    if int(summary.get("needs_review_count") or 0) > 0:
        return "review_ambiguous_or_unmatched_notes"
    return "no_alignment_action_needed"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(connection, table):
        return []
    return [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _select_expr(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode=ro"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only K-P-D import-completed evidence alignment hook dry-run."
        )
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--attachment-key")
    parser.add_argument("--zotero-item-key")
    parser.add_argument("--source-path")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_import_alignment_hook_report(
        args.db_path,
        document_id=args.document_id,
        attachment_key=args.attachment_key,
        zotero_item_key=args.zotero_item_key,
        source_path=args.source_path,
        limit=args.limit,
    )
    _emit(report, as_json=args.json)
    return 0 if not report["blockers"] else 2


def _emit(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            print(output, end="")
            return
        buffer.write(output.encode("utf-8"))
    else:
        print(dict(report))


if __name__ == "__main__":
    raise SystemExit(main())

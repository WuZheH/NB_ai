from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.unit_note_object_processing_service import (
    columns,
    connect_readonly,
    document_source_keys,
    find_unit_range,
    safety_flags,
    source_note_hash,
    table_exists,
)
from app.services import zotero_native_annotation_import_service


def sync_zotero_notes_for_unit(
    research_db: str | Path,
    zotero_db: str | Path,
    document_id: int,
    unit_type: str,
    unit_id: str | None = None,
    chapter_index: int | None = None,
    section_title: str | None = None,
    dry_run: bool = True,
    apply: bool = False,
) -> dict[str, Any]:
    unit = find_unit_range(research_db, document_id, unit_type, unit_id, chapter_index, section_title)
    with connect_readonly(research_db) as research_conn:
        source_keys = document_source_keys(research_conn, document_id)
        existing_keys = _existing_note_keys(research_conn)
    raw_notes = zotero_native_annotation_import_service._read_native_annotations(Path(zotero_db))
    candidates = [
        note
        for note in raw_notes
        if _matches_source(note, source_keys) and _matches_page(note, unit)
    ]
    would_insert = []
    skipped = []
    for note in candidates:
        annotation_key = note.get("zotero_annotation_key")
        hash_key = source_note_hash(
            note.get("zotero_attachment_key"),
            note.get("selected_text"),
            note.get("note_text"),
        )
        if annotation_key in existing_keys["annotation_keys"] or hash_key in existing_keys["selected_text_hashes"]:
            skipped.append({"reason": "duplicate_annotation_key_or_selected_text_hash", "note": note})
            continue
        prepared = {
            "source": "zotero_native_annotation",
            **note,
            "matched_document_id": document_id,
            "matched_chunk_ids_json": json.dumps(unit.get("chunk_ids", []), ensure_ascii=False),
            "evidence_alignment_status": "unit_page_range_matched" if unit.get("page_start") else "unit_matched_without_page_range",
            "sync_status": "dry_run" if dry_run or not apply else "synced",
            "review_status": "pending",
            "mechanism_status": "not_generated",
            "selected_text_hash": hash_key,
        }
        would_insert.append(prepared)
    report = {
        "status": "dry_run" if dry_run or not apply else "apply_requested",
        "mode": "unit_zotero_native_annotation_sync",
        "document_id": document_id,
        "unit": unit,
        "candidate_notes": candidates,
        "would_insert": would_insert,
        "skipped": skipped,
        "warnings": [unit["warning"]] if unit.get("warning") else [],
        **safety_flags(),
    }
    if apply and not dry_run:
        report.update(_apply_notes(research_db, would_insert))
    return report


def _read_zotero_annotations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if table_exists(conn, "annotations"):
        cols = columns(conn, "annotations")
        selected = [
            name
            for name in [
                "zotero_annotation_key",
                "zotero_item_key",
                "zotero_attachment_key",
                "pdf_page",
                "page_label",
                "selected_text",
                "note_text",
                "user_tags_json",
                "position_json",
            ]
            if name in cols
        ]
        rows = conn.execute(f"SELECT {', '.join(selected)} FROM annotations").fetchall()
        return [dict(row) for row in rows]
    if not table_exists(conn, "itemAnnotations"):
        return []
    cols = columns(conn, "itemAnnotations")
    selected = [name for name in ["key", "annotationKey", "itemKey", "parentItemKey", "annotationText", "annotationComment", "annotationPageLabel", "annotationPosition"] if name in cols]
    if not selected:
        return []
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM itemAnnotations").fetchall()
    notes = []
    for row in rows:
        data = dict(row)
        page_label = data.get("annotationPageLabel")
        notes.append(
            {
                "zotero_annotation_key": data.get("annotationKey") or data.get("key"),
                "zotero_item_key": data.get("itemKey"),
                "zotero_attachment_key": data.get("parentItemKey"),
                "pdf_page": _int_or_none(page_label),
                "page_label": page_label,
                "selected_text": data.get("annotationText"),
                "note_text": data.get("annotationComment"),
                "user_tags_json": "[]",
                "position_json": data.get("annotationPosition"),
            }
        )
    return notes


def _existing_note_keys(conn: sqlite3.Connection) -> dict[str, set[str]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return {"annotation_keys": set(), "selected_text_hashes": set()}
    cols = columns(conn, "zotero_inspiration_notes")
    selected = [name for name in ["zotero_annotation_key", "selected_text_hash"] if name in cols]
    if not selected:
        return {"annotation_keys": set(), "selected_text_hashes": set()}
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM zotero_inspiration_notes").fetchall()
    return {
        "annotation_keys": {row["zotero_annotation_key"] for row in rows if "zotero_annotation_key" in row.keys() and row["zotero_annotation_key"]},
        "selected_text_hashes": {row["selected_text_hash"] for row in rows if "selected_text_hash" in row.keys() and row["selected_text_hash"]},
    }


def _apply_notes(research_db: str | Path, notes: list[dict[str, Any]]) -> dict[str, Any]:
    conn = sqlite3.connect(research_db)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "zotero_inspiration_notes"):
            return {"status": "schema_gap", "db_write_performed": False, "schema_gaps": ["zotero_inspiration_notes_table_missing"]}
        available = columns(conn, "zotero_inspiration_notes")
        inserted = 0
        for note in notes:
            insert_cols = [key for key in note.keys() if key in available]
            if not insert_cols:
                continue
            placeholders = ", ".join("?" for _ in insert_cols)
            conn.execute(
                f"INSERT INTO zotero_inspiration_notes ({', '.join(insert_cols)}) VALUES ({placeholders})",
                [note[key] for key in insert_cols],
            )
            inserted += 1
        conn.commit()
        return {"status": "applied", "inserted_count": inserted, "db_write_performed": inserted > 0}
    finally:
        conn.close()


def _matches_source(note: dict[str, Any], source_keys: dict[str, Any]) -> bool:
    attachment = source_keys.get("zotero_attachment_key")
    item = source_keys.get("zotero_item_key")
    if attachment and note.get("zotero_attachment_key") == attachment:
        return True
    if item and note.get("zotero_item_key") == item:
        return True
    return not attachment and not item


def _matches_page(note: dict[str, Any], unit: dict[str, Any]) -> bool:
    page = _int_or_none(note.get("pdf_page") or note.get("page_label"))
    start = unit.get("page_start")
    end = unit.get("page_end") or start
    if page is None or start is None:
        return True
    return int(start) <= page <= int(end)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="R3-A dry-run Zotero native annotation sync for one document unit.")
    parser.add_argument("--research-db", required=True)
    parser.add_argument("--zotero-db", required=True)
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--unit-type", required=True, choices=["book_chapter", "paper_section", "whole_paper_unit"])
    parser.add_argument("--unit-id")
    parser.add_argument("--chapter-index", type=int)
    parser.add_argument("--section-title")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = sync_zotero_notes_for_unit(
        args.research_db,
        args.zotero_db,
        args.document_id,
        args.unit_type,
        unit_id=args.unit_id,
        chapter_index=args.chapter_index,
        section_title=args.section_title,
        dry_run=not args.apply,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

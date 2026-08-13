from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.retrieval.sources.markdown_note_adapter import parse_markdown_blocks


def _source_rows(note_files: list[Path], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in note_files:
        raw = path.read_text(encoding="utf-8")
        blocks = parse_markdown_blocks(raw) if path.suffix.lower() == ".md" else []
        if not blocks and path.suffix.lower() == ".txt" and raw.strip():
            blocks = [type("Block", (), {"text": raw.strip(), "heading_path": ()})()]
        for index, block in enumerate(blocks):
            content = str(block.text).strip()
            if not content:
                continue
            relative_path = path.resolve().relative_to(root.resolve()).as_posix()
            content_sha256 = hashlib.sha256(content.encode()).hexdigest()
            identity = hashlib.sha256(f"{relative_path}\0{index}\0{content_sha256}".encode()).hexdigest()
            rows.append({"path": path, "relative_path": relative_path, "block_ordinal": index, "content": content, "heading": " / ".join(getattr(block, "heading_path", ()) or ()), "identity": identity})
    return rows


def import_local_notes(*, db_path: str | Path, document_id: int, note_files: list[Path], inbox_root: Path) -> dict[str, Any]:
    rows = _source_rows(note_files, inbox_root)
    if not rows:
        return {"note_count": 0, "evidence_link_count": 0, "note_ids": []}
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    note_ids: list[int] = []
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for row in rows:
            is_markdown = row["path"].suffix.lower() == ".md"
            note_type = "local_note"
            kind = "local_markdown_block" if is_markdown else "local_text_note"
            columns = ["document_id", "note_type", "scope_type", "title", "content", "summary", "source_path", "content_hash", "source_system", "source_record_kind", "source_identity", "selected_text", "source_comment", "source_missing", "created_at", "updated_at"]
            values = [document_id, note_type, "document", row["heading"] or row["path"].stem, row["content"], None, row["relative_path"], hashlib.sha256(row["content"].encode()).hexdigest(), "chat_catalog", kind, row["identity"], "", row["content"], 0, now, now]
            placeholders = ",".join("?" for _ in values)
            cur = connection.execute(f"INSERT INTO personal_notes ({','.join(columns)}) VALUES ({placeholders})", values)
            note_id = int(cur.lastrowid)
            note_ids.append(note_id)
            connection.execute("""INSERT INTO note_evidence_links
                (note_id, document_id, chunk_id, link_type, evidence_role, quote_text,
                 confidence, created_by, created_at, pdf_page, page_label,
                 source_locator_json, alignment_status, alignment_method,
                 alignment_warnings_json, source_quote_hash)
                VALUES (?, ?, NULL, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL)""",
                (note_id, document_id, "document_context", "user_note", 1.0,
                 "chat_catalog", now,
                 json.dumps({"relative_path": row["relative_path"], "block_ordinal": row["block_ordinal"]}, ensure_ascii=False),
                 "document_only", "chat_catalog_bundle", "[]"))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"note_count": len(note_ids), "evidence_link_count": len(note_ids), "note_ids": note_ids}

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import PROJECT_ROOT


LARGE_CHUNK_THRESHOLD = 1000
LARGE_MARKDOWN_CHAR_THRESHOLD = 200_000
COPY_NOT_RECOMMENDED_CHAR_THRESHOLD = 500_000
EXPORT_DIR = PROJECT_ROOT / "outputs" / "zotero_markdown_exports"


class ZoteroMarkdownExportError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.candidates = candidates or []

    def detail(self) -> dict[str, Any]:
        detail = {
            "status": "ERROR",
            "error": self.code,
            "message": str(self),
            **_safety_flags(),
        }
        if self.candidates:
            detail["candidates"] = self.candidates
        return detail


def build_zotero_markdown_export(
    conn: sqlite3.Connection,
    *,
    zotero_attachment_key: str,
    zotero_item_key: str | None = None,
    save_to_file: bool = False,
) -> dict[str, Any]:
    attachment_key = _clean_required_key(zotero_attachment_key, "zotero_attachment_key")
    item_key = _clean_key(zotero_item_key)
    exported_at = datetime.now(timezone.utc).isoformat()

    candidates = _document_candidates(conn, attachment_key, item_key)
    notes = _notes_for_attachment(conn, attachment_key, None)
    resolution_status = "resolved"
    document: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] = []

    if len(candidates) > 1:
        raise ZoteroMarkdownExportError(
            "ambiguous_attachment_mapping",
            "Multiple NOTEBOOK_AI documents map to this Zotero attachment; pass a more specific key or resolve the duplicate import.",
            status_code=409,
            candidates=[_candidate_public(item) for item in candidates],
        )
    if len(candidates) == 1:
        document = candidates[0]
        chunks = _chunks_for_document(conn, int(document["document_id"]))
        notes = _notes_for_attachment(conn, attachment_key, int(document["document_id"]))
    else:
        resolution_status = "notes_only_no_document_mapping"

    if not chunks and not notes:
        raise ZoteroMarkdownExportError(
            "attachment_export_not_found",
            "No NOTEBOOK_AI document chunks or Zotero inspiration notes were found for this attachment.",
            status_code=404,
        )

    metadata = _metadata(
        document=document,
        attachment_key=attachment_key,
        item_key=item_key,
        exported_at=exported_at,
        chunks_count=len(chunks),
        notes_count=len(notes),
        resolution_status=resolution_status,
    )
    markdown = _render_markdown(metadata, chunks, notes)
    markdown_chars = len(markdown)
    large_export_warning = (
        len(chunks) > LARGE_CHUNK_THRESHOLD
        or markdown_chars > LARGE_MARKDOWN_CHAR_THRESHOLD
    )
    copy_not_recommended = markdown_chars > COPY_NOT_RECOMMENDED_CHAR_THRESHOLD
    output_path = _write_export_file(metadata, markdown) if save_to_file else None

    return {
        "status": "OK",
        "markdown": markdown,
        "metadata": metadata,
        "counts": {"chunks": len(chunks), "notes": len(notes)},
        "markdown_chars": markdown_chars,
        "large_export_warning": large_export_warning,
        "large_export_reasons": _large_export_reasons(len(chunks), markdown_chars),
        "copy_not_recommended": copy_not_recommended,
        "output_path": str(output_path) if output_path else None,
        **_safety_flags(),
    }


def _clean_required_key(value: str | None, name: str) -> str:
    key = _clean_key(value)
    if not key:
        raise ZoteroMarkdownExportError(
            "missing_required_key",
            f"{name} is required.",
            status_code=422,
        )
    return key


def _clean_key(value: str | None) -> str:
    return str(value or "").strip()


def _document_candidates(
    conn: sqlite3.Connection,
    attachment_key: str,
    item_key: str | None,
) -> list[dict[str, Any]]:
    rows = _fetchall(
        conn,
        """
        SELECT
            d.id AS document_id,
            d.title,
            d.source_path,
            d.pdf_path,
            d.zotero_key,
            ds.zotero_item_key,
            ds.zotero_attachment_key,
            ds.zotero_open_pdf_uri,
            zps.resolved_pdf_path,
            zps.attachment_path_raw
        FROM document_sources ds
        JOIN documents d ON d.id = ds.document_id
        LEFT JOIN zotero_pdf_sources zps
          ON zps.zotero_attachment_key = ds.zotero_attachment_key
         AND (zps.zotero_item_key = ds.zotero_item_key OR ds.zotero_item_key IS NULL)
        WHERE ds.zotero_attachment_key = ?
        ORDER BY d.id
        """,
        (attachment_key,),
    )
    if item_key:
        rows = [
            row for row in rows
            if item_key in {
                _clean_key(row.get("zotero_item_key")),
                _clean_key(row.get("zotero_key")),
            }
        ]
    return rows


def _chunks_for_document(conn: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    return _fetchall(
        conn,
        """
        SELECT
            k.id AS chunk_id,
            k.document_id,
            k.chunk_index,
            k.heading_path,
            k.chunk_text,
            k.pdf_page_start,
            k.pdf_page_end,
            k.chapter_id,
            bc.title AS chapter_title
        FROM knowledge_chunks k
        LEFT JOIN book_chapters bc ON bc.id = k.chapter_id
        WHERE k.document_id = ?
        ORDER BY
            CASE WHEN k.pdf_page_start IS NULL THEN 1 ELSE 0 END,
            k.pdf_page_start,
            k.chunk_index,
            k.id
        """,
        (document_id,),
    )


def _notes_for_attachment(
    conn: sqlite3.Connection,
    attachment_key: str,
    document_id: int | None,
) -> list[dict[str, Any]]:
    if document_id is None:
        rows = _fetchall(
            conn,
            """
            SELECT *
            FROM zotero_inspiration_notes
            WHERE zotero_attachment_key = ?
            ORDER BY CASE WHEN pdf_page IS NULL THEN 1 ELSE 0 END,
                     pdf_page,
                     created_at,
                     id
            """,
            (attachment_key,),
        )
    else:
        rows = _fetchall(
            conn,
            """
            SELECT *
            FROM zotero_inspiration_notes
            WHERE zotero_attachment_key = ?
               OR matched_document_id = ?
            ORDER BY CASE WHEN pdf_page IS NULL THEN 1 ELSE 0 END,
                     pdf_page,
                     created_at,
                     id
            """,
            (attachment_key, document_id),
        )
    deduped: dict[int, dict[str, Any]] = {}
    for row in rows:
        deduped[int(row["id"])] = row
    return list(deduped.values())


def _metadata(
    *,
    document: Mapping[str, Any] | None,
    attachment_key: str,
    item_key: str | None,
    exported_at: str,
    chunks_count: int,
    notes_count: int,
    resolution_status: str,
) -> dict[str, Any]:
    title = document.get("title") if document else None
    return {
        "document_id": document.get("document_id") if document else None,
        "title": title or f"Zotero attachment {attachment_key}",
        "zotero_item_key": (document.get("zotero_item_key") if document else None) or item_key,
        "zotero_attachment_key": attachment_key,
        "source_pdf": _source_pdf(document),
        "exported_at": exported_at,
        "chunks_count": chunks_count,
        "notes_count": notes_count,
        "document_resolution_status": resolution_status,
    }


def _source_pdf(document: Mapping[str, Any] | None) -> str:
    if not document:
        return ""
    for key in ("resolved_pdf_path", "pdf_path", "source_path", "attachment_path_raw"):
        value = _clean_key(document.get(key))
        if value:
            return value
    return ""


def _render_markdown(
    metadata: Mapping[str, Any],
    chunks: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> str:
    lines: list[str] = [
        f"# NOTEBOOK_AI Export: {metadata['title']}",
        "",
        "## Metadata",
        f"- document_id: {_field(metadata.get('document_id'))}",
        f"- zotero_item_key: {_field(metadata.get('zotero_item_key'))}",
        f"- zotero_attachment_key: {_field(metadata.get('zotero_attachment_key'))}",
        f"- source_pdf: {_field(metadata.get('source_pdf'))}",
        f"- exported_at: {_field(metadata.get('exported_at'))}",
        f"- chunks_count: {metadata.get('chunks_count', 0)}",
        f"- notes_count: {metadata.get('notes_count', 0)}",
        f"- document_resolution_status: {_field(metadata.get('document_resolution_status'))}",
        "",
        "## How to use this file in ChatGPT",
        "请基于本文件回答问题时：",
        "1. 区分 PDF 原文证据和用户笔记；",
        "2. 每个关键结论引用 stable ID；",
        "3. 不要编造没有证据支持的结论；",
        "4. 可以按主题簇整理；",
        "5. 可以提出后续检索关键词。",
        "",
        "## PDF Chunks",
        "",
    ]
    if not chunks:
        lines.extend(["_No PDF chunks were found for this attachment._", ""])
    for chunk in chunks:
        lines.extend(_chunk_markdown(chunk))
    lines.extend(["## User Notes", ""])
    if not notes:
        lines.extend(["_No Zotero inspiration notes were found for this attachment._", ""])
    for note in notes:
        lines.extend(_note_markdown(note))
    lines.extend(_combined_view_markdown(chunks, notes))
    lines.extend(
        [
            "## Suggested ChatGPT Task",
            "请基于以上 PDF 原文片段和用户笔记：",
            "1. 按主题簇整理材料；",
            "2. 区分原文证据、用户笔记理解和模型推断；",
            "3. 提炼可迁移机制或研究启发；",
            "4. 指出还需要继续查哪些关键词；",
            "5. 每个关键结论引用对应 stable ID。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _chunk_markdown(chunk: Mapping[str, Any]) -> list[str]:
    stable_id = _chunk_stable_id(chunk)
    page = _chunk_page(chunk)
    return [
        f"### [{stable_id}]",
        "- type: pdf_chunk",
        f"- page: {_field(page)}",
        f"- heading_path: {_field(chunk.get('heading_path') or chunk.get('chapter_title'))}",
        f"- chunk_id: {_field(chunk.get('chunk_id'))}",
        "",
        _field(chunk.get("chunk_text")),
        "",
    ]


def _note_markdown(note: Mapping[str, Any]) -> list[str]:
    stable_id = _note_stable_id(note)
    tags = _json_list(note.get("user_tags_json"))
    return [
        f"### [{stable_id}]",
        "- type: zotero_inspiration_note",
        f"- note_id: {_field(note.get('server_note_id') or note.get('id'))}",
        f"- page: {_field(note.get('pdf_page'))}",
        f"- linked_document_id: {_field(note.get('matched_document_id'))}",
        f"- linked_chunk_id: {_field(note.get('matched_chunk_id'))}",
        f"- tags: {', '.join(str(tag) for tag in tags) if tags else ''}",
        "",
        "#### Selected Text",
        _blockquote(_field(note.get("selected_text"))),
        "",
        "#### My Note",
        _field(note.get("note_text")),
        "",
    ]


def _combined_view_markdown(
    chunks: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> list[str]:
    pages = sorted(
        {
            _combined_page_key(_chunk_page(chunk))
            for chunk in chunks
        }
        | {
            _combined_page_key(note.get("pdf_page"))
            for note in notes
        },
        key=_combined_page_sort_key,
    )
    lines = ["## Combined Evidence View", ""]
    if not pages:
        return [*lines, "_No page-level evidence is available._", ""]
    for page_key in pages:
        page_chunks = [chunk for chunk in chunks if _combined_page_key(_chunk_page(chunk)) == page_key]
        page_notes = [note for note in notes if _combined_page_key(note.get("pdf_page")) == page_key]
        lines.extend(
            [
                f"### p.{page_key}",
                "- PDF chunks:",
                *[f"  - [{_chunk_stable_id(chunk)}]" for chunk in page_chunks],
            ]
        )
        if not page_chunks:
            lines.append("  - none")
        lines.append("- User notes:")
        lines.extend([f"  - [{_note_stable_id(note)}]" for note in page_notes])
        if not page_notes:
            lines.append("  - none")
        lines.append("")
    return lines


def _chunk_stable_id(chunk: Mapping[str, Any]) -> str:
    document_id = chunk.get("document_id")
    page = _page_token(_chunk_page(chunk))
    return f"DOC{document_id}-{page}-C{chunk.get('chunk_id')}"


def _note_stable_id(note: Mapping[str, Any]) -> str:
    server_note_id = _clean_key(note.get("server_note_id"))
    if server_note_id:
        return f"NOTE-{server_note_id}"
    return f"NOTE-row-{note.get('id')}"


def _chunk_page(chunk: Mapping[str, Any]) -> Any:
    return chunk.get("pdf_page_start") or chunk.get("pdf_page_end")


def _page_token(page: Any) -> str:
    value = _clean_key(page)
    return f"P{value}" if value else "PNA"


def _combined_page_key(page: Any) -> str:
    return _clean_key(page) or "unknown"


def _combined_page_sort_key(page: str) -> tuple[int, int | str]:
    number = int(page) if str(page).isdigit() else None
    return (0, number) if number is not None else (1, page)


def _large_export_reasons(chunks_count: int, markdown_chars: int) -> list[str]:
    reasons: list[str] = []
    if chunks_count > LARGE_CHUNK_THRESHOLD:
        reasons.append("chunks_count_gt_1000")
    if markdown_chars > LARGE_MARKDOWN_CHAR_THRESHOLD:
        reasons.append("markdown_chars_gt_200000")
    if markdown_chars > COPY_NOT_RECOMMENDED_CHAR_THRESHOLD:
        reasons.append("copy_not_recommended_gt_500000")
    return reasons


def _write_export_file(metadata: Mapping[str, Any], markdown: str) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _safe_slug(str(metadata.get("title") or "zotero-export"))
    attachment_key = _safe_slug(str(metadata.get("zotero_attachment_key") or "attachment"))
    base = EXPORT_DIR / f"{slug}_{attachment_key}_{timestamp}.md"
    path = base
    for index in range(1, 100):
        if not path.exists():
            path.write_text(markdown, encoding="utf-8")
            return path
        path = EXPORT_DIR / f"{slug}_{attachment_key}_{timestamp}_{index}.md"
    raise ZoteroMarkdownExportError(
        "export_file_name_exhausted",
        "Could not allocate a unique export filename.",
        status_code=500,
    )


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return slug[:80] or "notebook-ai-export"


def _candidate_public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "document_id": item.get("document_id"),
        "title": item.get("title"),
        "zotero_item_key": item.get("zotero_item_key"),
        "zotero_attachment_key": item.get("zotero_attachment_key"),
    }


def _field(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _blockquote(value: str) -> str:
    text = _field(value)
    if not text:
        return "> "
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _json_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _fetchall(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "external_api_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "mechanism_card_created": False,
        "vector_store_write_performed": False,
    }

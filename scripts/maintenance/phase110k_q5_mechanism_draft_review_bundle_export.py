from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


MODE = "phase110k_q5_mechanism_draft_review_bundle_export_v1"
DEFAULT_DB_PATH = Path("data/db/research_memory.db")
DEFAULT_OUTPUT_JSON = Path("tmp/phase110k_q5_mechanism_draft_review_bundle.json")
DEFAULT_OUTPUT_MD = Path("tmp/phase110k_q5_mechanism_draft_review_bundle.md")
REVIEW_OPTIONS = [
    {
        "action": "accept",
        "review_status": "accepted",
        "review_decision": "accepted",
        "meaning": "Accept this draft only; final mechanism card is not created in K-Q5.",
    },
    {
        "action": "reject",
        "review_status": "rejected",
        "review_decision": "rejected",
        "meaning": "Reject this draft candidate.",
    },
    {
        "action": "needs_edit",
        "review_status": "needs_edit",
        "review_decision": "needs_edit",
        "meaning": "Send the draft back for revision or a new paste-back candidate.",
    },
    {
        "action": "defer",
        "review_status": "deferred",
        "review_decision": "deferred",
        "meaning": "Postpone the review decision.",
    },
    {
        "action": "merge_into",
        "review_status": "merged",
        "review_decision": "merged",
        "meaning": "Mark this draft as merged into another existing draft.",
    },
]
SUGGESTED_REVIEW_QUESTIONS = [
    "这个机制是否真的被原文支持？",
    "哪些部分是用户解释？",
    "哪些部分是 speculative extension？",
    "关联 objects 是否合理？",
    "是否适合进入正式机制卡？",
]


def export_mechanism_draft_review_bundle(
    db_path: str | Path,
    *,
    draft_id: str,
    output_json_path: str | Path = DEFAULT_OUTPUT_JSON,
    output_md_path: str | Path = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    warnings: list[str] = []
    blockers: list[str] = []
    try:
        conn = _connect_readonly(Path(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return _result(
            status="FAIL",
            draft_id=draft_id,
            output_json_path=output_json_path,
            output_md_path=output_md_path,
            blockers=[f"db_open_failed:{exc}"],
            warnings=warnings,
        )

    try:
        row = _fetch_draft(conn, draft_id)
        if row is None:
            return _result(
                status="BLOCKED",
                draft_id=draft_id,
                output_json_path=output_json_path,
                output_md_path=output_md_path,
                blockers=["mechanism_draft_candidate_not_found"],
                warnings=warnings,
            )
        bundle = _build_bundle(conn, row, warnings)
    finally:
        conn.close()

    output_json = Path(output_json_path)
    output_md = Path(output_md_path)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_md.write_text(_render_markdown(bundle), encoding="utf-8")

    return _result(
        status="OK",
        draft_id=draft_id,
        output_json_path=output_json,
        output_md_path=output_md,
        blockers=blockers,
        warnings=warnings,
        bundle_summary=_bundle_summary(bundle),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a human-readable review bundle for a mechanism draft candidate."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = export_mechanism_draft_review_bundle(
        args.db_path,
        draft_id=args.draft_id,
        output_json_path=args.output_json,
        output_md_path=args.output_md,
    )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0 if report["status"] == "OK" else 1


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _fetch_draft(conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM mechanism_draft_candidates WHERE draft_id = ?",
        (draft_id,),
    ).fetchone()


def _build_bundle(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    warnings: list[str],
) -> dict[str, Any]:
    candidate = _json_object(row["draft_json"])
    validation_report = _json_object(row["validation_report_json"])
    prompt_export_metadata = _json_object(row["prompt_export_metadata_json"])
    paste_back_context = _json_object(row["paste_back_readiness_context_json"])
    source_note_ids = _json_list(row["source_inspiration_note_ids_json"])
    evidence_chunk_ids = _int_list(_json_list(row["evidence_chunk_ids_json"]))
    pdf_pages = _json_list(row["pdf_pages_json"])
    source_note = _source_note(conn, source_note_ids[0] if source_note_ids else None, warnings)
    evidence = _evidence(conn, row, evidence_chunk_ids, pdf_pages, warnings)
    object_ids = _object_ids(candidate, source_note)
    objects = _objects(conn, object_ids, warnings)
    return {
        "status": "OK",
        "mode": MODE,
        "draft": {
            "draft_id": row["draft_id"],
            "mechanism_key": row["mechanism_key"],
            "mechanism_name_cn": row["mechanism_name_cn"],
            "mechanism_name_en": row["mechanism_name_en"],
            "mechanism_type": row["mechanism_type"],
            "confidence": row["confidence"],
            "review_status": row["review_status"],
            "review_decision": row["review_decision"],
            "review_notes": row["review_notes"],
            "created_at": row["created_at"],
        },
        "candidate": candidate,
        "source_note": source_note,
        "evidence": evidence,
        "objects": objects,
        "validation": {
            "validation_report_json": validation_report,
            "status": validation_report.get("status"),
            "errors": validation_report.get("errors") or [],
            "warnings": validation_report.get("warnings") or [],
        },
        "prompt_metadata": {
            "prompt_export_metadata_json": prompt_export_metadata,
            "paste_back_readiness_context_json": paste_back_context,
        },
        "review_options": REVIEW_OPTIONS,
        "suggested_review_questions": SUGGESTED_REVIEW_QUESTIONS,
        "warnings": list(dict.fromkeys(warnings)),
        **_safety_flags(),
    }


def _source_note(
    conn: sqlite3.Connection,
    source_note_id: Any,
    warnings: list[str],
) -> dict[str, Any]:
    if source_note_id is None:
        warnings.append("source_note_id_missing")
        return {}
    if not _table_exists(conn, "zotero_inspiration_notes"):
        warnings.append("source_note_table_missing")
        return {"server_note_id": source_note_id}
    columns = _table_columns(conn, "zotero_inspiration_notes")
    wanted = [
        "server_note_id",
        "client_note_id",
        "note_text",
        "selected_text",
        "pdf_page",
        "page_label",
        "zotero_attachment_key",
        "zotero_item_key",
        "evidence_alignment_status",
        "alignment_confidence",
        "matched_document_id",
        "matched_object_ids_json",
    ]
    selected = [column for column in wanted if column in columns]
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM zotero_inspiration_notes WHERE server_note_id = ?",
        (str(source_note_id),),
    ).fetchone()
    if row is None:
        warnings.append("source_note_not_found")
        return {"server_note_id": source_note_id}
    payload = _row_dict(row)
    for key in wanted:
        payload.setdefault(key, None)
    return payload


def _evidence(
    conn: sqlite3.Connection,
    draft_row: sqlite3.Row,
    chunk_ids: list[int],
    pdf_pages: list[Any],
    warnings: list[str],
) -> dict[str, Any]:
    chunk_previews: list[dict[str, Any]] = []
    if not chunk_ids:
        warnings.append("evidence_chunk_ids_missing")
    elif not _table_exists(conn, "knowledge_chunks"):
        warnings.append("knowledge_chunks_table_missing")
    else:
        columns = _table_columns(conn, "knowledge_chunks")
        wanted = [
            "id",
            "document_id",
            "chunk_index",
            "heading_path",
            "chunk_text",
            "pdf_page_start",
            "pdf_page_end",
            "zotero_open_url",
        ]
        selected = [column for column in wanted if column in columns]
        placeholders = ", ".join("?" for _ in chunk_ids)
        rows = conn.execute(
            f"""
            SELECT {', '.join(selected)}
            FROM knowledge_chunks
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            tuple(chunk_ids),
        ).fetchall()
        by_id = {_row_dict(row)["id"]: _row_dict(row) for row in rows}
        missing_ids = [chunk_id for chunk_id in chunk_ids if chunk_id not in by_id]
        if missing_ids:
            warnings.append("evidence_chunk_metadata_unresolved")
        for chunk_id in chunk_ids:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                chunk_previews.append({"chunk_id": chunk_id, "resolved": False})
                continue
            text = str(chunk.get("chunk_text") or "")
            chunk_previews.append(
                {
                    "chunk_id": chunk_id,
                    "resolved": True,
                    "document_id": chunk.get("document_id"),
                    "chunk_index": chunk.get("chunk_index"),
                    "heading_path": chunk.get("heading_path"),
                    "chunk_text_preview": _truncate(text, 1200),
                    "pdf_page_start": chunk.get("pdf_page_start"),
                    "pdf_page_end": chunk.get("pdf_page_end"),
                    "zotero_open_url": chunk.get("zotero_open_url"),
                }
            )
    return {
        "matched_document_id": draft_row["matched_document_id"],
        "evidence_chunk_ids": chunk_ids,
        "chunk_previews": chunk_previews,
        "pdf_pages": pdf_pages,
    }


def _object_ids(candidate: Mapping[str, Any], source_note: Mapping[str, Any]) -> list[int]:
    values: list[int] = []
    values.extend(_int_list(candidate.get("source_object_ids")))
    values.extend(_int_list(_json_list(source_note.get("matched_object_ids_json"))))
    return list(dict.fromkeys(values))


def _objects(
    conn: sqlite3.Connection,
    object_ids: list[int],
    warnings: list[str],
) -> dict[str, Any]:
    if not object_ids:
        warnings.append("object_ids_missing")
        return {"object_ids": [], "items": [], "resolved": False}
    if not _table_exists(conn, "object_candidates"):
        warnings.append("object_metadata_unresolved")
        return {
            "object_ids": object_ids,
            "items": [{"object_id": object_id, "resolved": False} for object_id in object_ids],
            "resolved": False,
        }
    columns = _table_columns(conn, "object_candidates")
    wanted = ["id", "object_key", "object_name", "object_type", "review_status"]
    selected = [column for column in wanted if column in columns]
    placeholders = ", ".join("?" for _ in object_ids)
    rows = conn.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM object_candidates
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(object_ids),
    ).fetchall()
    by_id = {_row_dict(row)["id"]: _row_dict(row) for row in rows}
    unresolved = [object_id for object_id in object_ids if object_id not in by_id]
    if unresolved:
        warnings.append("object_metadata_unresolved")
    items = []
    for object_id in object_ids:
        row = by_id.get(object_id)
        if row is None:
            items.append({"object_id": object_id, "resolved": False})
            continue
        items.append(
            {
                "object_id": object_id,
                "object_key": row.get("object_key"),
                "object_name": row.get("object_name"),
                "object_type": row.get("object_type"),
                "review_status": row.get("review_status"),
                "resolved": True,
            }
        )
    return {
        "object_ids": object_ids,
        "items": items,
        "resolved": not unresolved,
    }


def _render_markdown(bundle: Mapping[str, Any]) -> str:
    draft = _mapping(bundle.get("draft"))
    candidate = _mapping(bundle.get("candidate"))
    source_note = _mapping(bundle.get("source_note"))
    evidence = _mapping(bundle.get("evidence"))
    validation = _mapping(bundle.get("validation"))
    objects = _mapping(bundle.get("objects"))
    draft_id = str(draft.get("draft_id") or "")
    lines = [
        f"# Mechanism Draft Review Bundle: {draft_id}",
        "",
        "## Current Status",
        "",
        f"- review_status: `{draft.get('review_status')}`",
        f"- review_decision: `{draft.get('review_decision')}`",
        f"- mechanism_key: `{draft.get('mechanism_key')}`",
        f"- mechanism_type: `{draft.get('mechanism_type')}`",
        f"- confidence: `{draft.get('confidence')}`",
        "",
        "## Mechanism Draft Summary",
        "",
        f"- Name: {candidate.get('mechanism_name') or draft.get('mechanism_name_cn') or ''}",
        f"- Summary: {candidate.get('mechanism_summary') or ''}",
        f"- Evidence support level: `{candidate.get('evidence_support_level')}`",
        "",
        "### User Interpretation",
        "",
        _text_block(candidate.get("user_interpretation")),
        "",
        "### Generalized Mechanism",
        "",
        _text_block(candidate.get("generalized_mechanism")),
        "",
        "### Transferable Pattern",
        "",
        _text_block(candidate.get("transferable_pattern")),
        "",
        "## Original Zotero Note",
        "",
        f"- server_note_id: `{source_note.get('server_note_id')}`",
        f"- client_note_id: `{source_note.get('client_note_id')}`",
        f"- zotero_item_key: `{source_note.get('zotero_item_key')}`",
        f"- zotero_attachment_key: `{source_note.get('zotero_attachment_key')}`",
        f"- pdf_page/page_label: `{source_note.get('pdf_page')}` / `{source_note.get('page_label')}`",
        f"- evidence_alignment_status: `{source_note.get('evidence_alignment_status')}`",
        f"- alignment_confidence: `{source_note.get('alignment_confidence')}`",
        "",
        "### note_text",
        "",
        _text_block(source_note.get("note_text")),
        "",
        "### selected_text",
        "",
        _text_block(source_note.get("selected_text")),
        "",
        "## Evidence Chunks",
        "",
        f"- matched_document_id: `{evidence.get('matched_document_id')}`",
        f"- evidence_chunk_ids: `{evidence.get('evidence_chunk_ids')}`",
        f"- pdf_pages: `{evidence.get('pdf_pages')}`",
        "",
    ]
    for chunk in evidence.get("chunk_previews") or []:
        if not isinstance(chunk, Mapping):
            continue
        lines.extend(
            [
                f"### Chunk {chunk.get('chunk_id')}",
                "",
                f"- resolved: `{chunk.get('resolved')}`",
                f"- heading_path: `{chunk.get('heading_path')}`",
                f"- pdf_page_start/pdf_page_end: `{chunk.get('pdf_page_start')}` / `{chunk.get('pdf_page_end')}`",
                "",
                _text_block(chunk.get("chunk_text_preview")),
                "",
            ]
        )
    lines.extend(["## Linked Objects", ""])
    for item in objects.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        label = item.get("object_name") or "unresolved"
        lines.append(
            f"- `{item.get('object_id')}` | {label} | {item.get('object_type')} | resolved={item.get('resolved')}"
        )
    lines.extend(
        [
            "",
            "## Validator Result",
            "",
            f"- status: `{validation.get('status')}`",
            f"- errors: `{validation.get('errors')}`",
            f"- warnings: `{validation.get('warnings')}`",
            "",
            "## Review Questions",
            "",
        ]
    )
    lines.extend(f"- {question}" for question in bundle.get("suggested_review_questions") or [])
    lines.extend(["", "## Review Options", ""])
    for option in bundle.get("review_options") or []:
        if isinstance(option, Mapping):
            lines.append(
                f"- `{option.get('action')}` -> `{option.get('review_status')}`: {option.get('meaning')}"
            )
    lines.extend(
        [
            "",
            "## CLI Review Command Examples",
            "",
            "```powershell",
            _review_command(draft_id, "accept", "Accept draft after human review"),
            _review_command(draft_id, "reject", "Reject draft after human review"),
            _review_command(draft_id, "needs_edit", "Needs edit after human review"),
            _review_command(draft_id, "defer", "Defer draft review"),
            "```",
            "",
            "K-Q5 exports review material only. It does not change review_status and does not create a final mechanism_card.",
            "",
        ]
    )
    return "\n".join(lines)


def _review_command(draft_id: str, action: str, note: str) -> str:
    python_path = r"D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe"
    return (
        f'{python_path} scripts\\phase110k_q4_mechanism_draft_review_cli.py '
        f"--db-path data/db/research_memory.db --draft-id {draft_id} "
        f'--action {action} --review-notes "{note}" --dry-run --json'
    )


def _result(
    *,
    status: str,
    draft_id: str,
    output_json_path: str | Path,
    output_md_path: str | Path,
    blockers: list[str],
    warnings: list[str],
    bundle_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": MODE,
        "draft_id": draft_id,
        "output_json_path": str(output_json_path),
        "output_md_path": str(output_md_path),
        "bundle_summary": dict(bundle_summary or {}),
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        **_safety_flags(),
    }


def _bundle_summary(bundle: Mapping[str, Any]) -> dict[str, Any]:
    draft = _mapping(bundle.get("draft"))
    evidence = _mapping(bundle.get("evidence"))
    objects = _mapping(bundle.get("objects"))
    validation = _mapping(bundle.get("validation"))
    return {
        "review_status": draft.get("review_status"),
        "mechanism_name_cn": draft.get("mechanism_name_cn"),
        "mechanism_type": draft.get("mechanism_type"),
        "confidence": draft.get("confidence"),
        "evidence_chunk_ids": evidence.get("evidence_chunk_ids") or [],
        "object_count": len(objects.get("items") or []),
        "validation_status": validation.get("status"),
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    results: list[int] = []
    for item in value:
        try:
            integer = int(item)
        except (TypeError, ValueError):
            continue
        if integer not in results:
            results.append(integer)
    return results


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _text_block(value: Any) -> str:
    text = str(value or "").strip()
    return f"```\n{text}\n```"


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "review_status_changed": False,
        "mechanism_card_generated": False,
        "mechanism_card_created": False,
        "llm_called": False,
        "api_called": False,
        "vector_store_write_performed": False,
        "knowledge_chunks_write_performed": False,
        "zotero_inspiration_notes_write_performed": False,
        "zotero_source_db_write_performed": False,
    }


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

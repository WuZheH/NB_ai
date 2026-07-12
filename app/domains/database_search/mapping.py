"""Pure database-value to API-value mapping helpers."""

from __future__ import annotations

import json
from typing import Any


def _stable_chunk_id(document_id: Any, page: Any, chunk_id: Any) -> str:
    page_part = f"P{page}" if page not in (None, "") else "PNA"
    return f"DOC{document_id}-{page_part}-C{chunk_id}"


def _stable_note_id(note_identity: Any) -> str:
    text = str(note_identity or "").strip()
    if not text:
        return "NOTE-unknown"
    return f"NOTE-{text[:14]}"


def _note_identity(row: dict[str, Any]) -> str:
    return str(
        row.get("server_note_id")
        or row.get("note_id")
        or row.get("client_note_id")
        or row.get("zotero_annotation_key")
        or row.get("id")
        or ""
    )


def _numeric_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_ints(value: Any) -> list[int]:
    values = _json_list(value)
    result: list[int] = []
    for item in values:
        try:
            integer = int(item)
        except (TypeError, ValueError):
            continue
        if integer > 0 and integer not in result:
            result.append(integer)
    return result


def _first_int(values: list[Any]) -> int | None:
    for value in values:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _json_list(value: Any) -> list[Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, list) else []


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "zotero_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "vector_write_performed": False,
        "vector_store_write_performed": False,
    }


__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")]

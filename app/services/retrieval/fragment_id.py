from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid5

from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalSourceType,
)

_LOCATOR_FIELDS: dict[str, tuple[str, ...]] = {
    "pdf_chunk": ("document_id", "chunk_id"),
    "zotero_highlight": ("library_id", "annotation_key"),
    "zotero_annotation_comment": ("library_id", "annotation_key"),
    "zotero_child_note": ("library_id", "item_key"),
    "personal_note": ("row_id",),
    "markdown_note": ("relative_path", "heading_path", "block_ordinal"),
}


def canonical_source_locator(source_type: RetrievalSourceType, **identity: Any) -> str:
    if source_type == "zotero_inspiration_note":
        fields = ("server_note_id",) if _present(identity.get("server_note_id")) else ("row_id",)
    else:
        fields = _LOCATOR_FIELDS[source_type]

    missing = [name for name in fields if not _present(identity.get(name))]
    if missing:
        raise ValueError(f"missing canonical identity fields for {source_type}: {', '.join(missing)}")

    parts = [source_type]
    for name in fields:
        value = identity[name]
        if name == "relative_path":
            value = PurePosixPath(str(value).replace("\\", "/")).as_posix()
        parts.append(f"{name}={_encode(value)}")
    return "|".join(parts)


def fragment_uuid(canonical_locator: str) -> str:
    if not canonical_locator.strip():
        raise ValueError("canonical locator must not be empty")
    return str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, canonical_locator))


def related_uuid(group_kind: str, canonical_locator: str) -> str:
    if not group_kind.strip() or not canonical_locator.strip():
        raise ValueError("group kind and locator must not be empty")
    return str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, f"group|{group_kind}|{canonical_locator}"))


def annotation_source_group_id(library_id: int, annotation_key: str) -> str:
    return related_uuid(
        "zotero_annotation",
        f"library_id={library_id}|annotation_key={annotation_key}",
    )


def annotation_duplicate_group_id(library_id: int, annotation_key: str) -> str:
    return related_uuid(
        "zotero_annotation_duplicate",
        f"library_id={library_id}|annotation_key={annotation_key}",
    )


def display_id_for(
    source_type: RetrievalSourceType,
    *,
    fragment_id: str,
    document_id: int | None = None,
    page_number: int | None = None,
    source_record_id: str,
) -> str:
    if source_type == "pdf_chunk":
        page = str(page_number) if page_number is not None else "NA"
        return f"DOC{document_id}-P{page}-C{source_record_id}"
    prefixes = {
        "zotero_highlight": "ZHL",
        "zotero_annotation_comment": "ZCOMMENT",
        "zotero_child_note": "ZNOTE",
        "zotero_inspiration_note": "ZINSP",
        "personal_note": "PNOTE",
        "markdown_note": "MD",
    }
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", source_record_id).strip("-")
    if readable and len(readable) <= 40:
        return f"{prefixes[source_type]}-{readable}"
    return f"{prefixes[source_type]}-{UUID(fragment_id).hex[:12]}"


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _encode(value: Any) -> str:
    return quote(str(value).strip(), safe="/.:_-")

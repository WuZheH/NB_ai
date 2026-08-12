from __future__ import annotations

from typing import Any


def add_citation_fields(item: dict[str, Any], layer: str) -> dict[str, Any]:
    enriched = dict(item)
    source_type = _source_type(enriched, layer)
    locator = _source_locator(enriched, source_type)
    tokens = _citation_tokens(enriched, locator, source_type)
    enriched["citation_tokens"] = tokens
    enriched["citation_label"] = _citation_label(enriched, locator, source_type)
    enriched["source_locator"] = locator
    enriched["locator"] = locator
    enriched["source_type"] = source_type
    enriched["source_title"] = _source_title(enriched, source_type)
    enriched["page_label"] = locator.get("page_label") or enriched.get("page_label") or ""
    enriched["chapter_label"] = _chapter_label(enriched, locator)
    return enriched


def add_citations_to_results(results: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        layer: [add_citation_fields(item, layer) for item in items]
        for layer, items in results.items()
    }


def _source_type(item: dict[str, Any], layer: str) -> str:
    if layer == "evidence_chunks":
        return "chunk"
    if layer == "zotero_notes":
        return "note"
    if layer == "objects":
        return "object_candidate"
    if layer == "mechanisms":
        return "mechanism"
    value = str(item.get("source_type") or item.get("source_kind") or layer)
    return "mechanism" if value == "mechanism_evidence" else value


def _source_locator(item: dict[str, Any], source_type: str) -> dict[str, Any]:
    chunk_ids = list(item.get("source_chunk_ids") or item.get("matched_chunk_ids") or [])
    chunk_id = item.get("chunk_id") or (chunk_ids[0] if chunk_ids else None)
    pdf_page = item.get("pdf_page") or item.get("page")
    locator = {
        "source_type": source_type,
        "document_id": item.get("document_id"),
        "document_title": item.get("document_title") or "",
        "chapter_id": item.get("chapter_id"),
        "chapter_label": _chapter_label(item, {}),
        "chunk_id": chunk_id,
        "chunk_ids": chunk_ids or ([chunk_id] if chunk_id is not None else []),
        "pdf_page": pdf_page,
        "page_label": item.get("page_label") or (f"p.{pdf_page}" if pdf_page else ""),
        "note_id": item.get("note_id") or item.get("server_note_id") or item.get("client_note_id"),
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "object_candidate_id": item.get("object_candidate_id"),
        "mechanism_candidate_id": item.get("draft_id") or item.get("id"),
    }
    return {key: value for key, value in locator.items() if value not in (None, "", [])}


def _citation_tokens(
    item: dict[str, Any],
    locator: dict[str, Any],
    source_type: str,
) -> list[str]:
    tokens: list[str] = []
    _append_token(tokens, "doc", locator.get("document_id"))
    _append_token(tokens, "chapter", locator.get("chapter_id"))
    for chunk_id in locator.get("chunk_ids") or []:
        _append_token(tokens, "chunk", chunk_id)
    _append_token(tokens, "page", _page_token(locator.get("page_label") or locator.get("pdf_page")))
    if source_type == "note":
        _append_token(tokens, "note", locator.get("note_id"))
    if source_type == "object_candidate":
        _append_token(tokens, "object", locator.get("object_candidate_id"))
    if source_type == "mechanism":
        _append_token(tokens, "mechanism", item.get("draft_id") or item.get("id"))
    return tokens


def _citation_label(item: dict[str, Any], locator: dict[str, Any], source_type: str) -> str:
    title = _source_title(item, source_type)
    page = locator.get("page_label") or "page n/a"
    chapter = _chapter_label(item, locator)
    if source_type == "chunk":
        suffix = f"chunk {locator.get('chunk_id')}" if locator.get("chunk_id") else "chunk n/a"
        return _join_label(title, chapter, page, suffix)
    if source_type == "note":
        note = locator.get("note_id") or "note n/a"
        return _join_label("Zotero note", title, page, str(note))
    if source_type == "object_candidate":
        name = item.get("object_name") or item.get("label") or "object"
        return _join_label("Object", str(name), title, page)
    if source_type == "mechanism":
        name = item.get("label") or item.get("title") or "mechanism draft"
        return _join_label("Mechanism draft", str(name), title, page)
    return _join_label(title, page)


def _source_title(item: dict[str, Any], source_type: str) -> str:
    if source_type == "object_candidate":
        return str(item.get("document_title") or item.get("object_name") or item.get("title") or "Object source")
    if source_type == "mechanism":
        return str(item.get("document_title") or item.get("title") or "Mechanism source")
    return str(item.get("document_title") or item.get("title") or "Untitled source")


def _chapter_label(item: dict[str, Any], locator: dict[str, Any]) -> str:
    return str(
        item.get("chapter_label")
        or item.get("chapter_title")
        or locator.get("chapter_label")
        or item.get("heading_path")
        or ""
    )


def _append_token(tokens: list[str], prefix: str, value: Any) -> None:
    text = _token_value(value)
    if not text:
        return
    token = f"{prefix}:{text}"
    if token not in tokens:
        tokens.append(token)


def _token_value(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace(" ", "_").replace(":", "_")


def _page_token(value: Any) -> str:
    return _token_value(str(value).replace("p.", "p") if value is not None else "")


def _join_label(*parts: str) -> str:
    clean = [part for part in (str(item or "").strip() for item in parts) if part]
    return " · ".join(clean)

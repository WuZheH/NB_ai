from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api import retrieval_api
from app.domains.retrieval.fragment_locator_service import (
    FragmentLocatorNotFound,
    build_fragment_locator,
)
from app.domains.retrieval.fragment_repository import NotebookFragmentRecord
from app.domains.retrieval.locator_contracts import FragmentLocator
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget
from app.services.retrieval.fragment_id import canonical_source_locator
from app.services.retrieval.sources._common import make_fragment


def _record(
    *,
    text: str = "fixture source text",
    page: int | None = 4,
    attachment_key: str | None = "ATTACHMENT",
    bbox: dict[str, Any] | None = None,
) -> NotebookFragmentRecord:
    source = make_fragment(
        source_type="pdf_chunk",
        origin_kind="manual_import",
        source_record_id="21",
        canonical_locator=canonical_source_locator(
            "pdf_chunk", document_id=8, chunk_id=21
        ),
        text=text,
        adapter_version="locator-contract.v1",
        document_id=8,
        zotero_item_key="ITEM",
        zotero_attachment_key=attachment_key,
        page_number=page,
        page_label=str(page) if page else None,
        bbox=bbox,
        original_file_path="private-paper.pdf",
        provenance=[{"store": "fixture"}],
    )
    fragment = NotebookFragment(
        fragment_id=source.fragment_id,
        source_type="pdf_chunk",
        zotero_item_key="ITEM",
        zotero_attachment_key=attachment_key,
        document_id=8,
        document_title="Fixture Paper",
        chunk_id=21,
        pdf_page=page,
        page_label=str(page) if page else None,
        text=text,
        tags=[],
        content_hash=source.content_hash,
        provenance=[{"store": "fixture"}],
        open_target=OpenTarget(),
    )
    return NotebookFragmentRecord(fragment=fragment, source=source)


def test_bbox_locator_is_minimal_and_does_not_expose_source_path() -> None:
    locator = build_fragment_locator(
        _record(
            text="x" * 700,
            bbox={
                "pageIndex": 3,
                "rects": [[1.0, 2.0, 3.0, 4.0]],
                "private_path": "must-not-leak",
            },
        )
    )
    payload = locator.model_dump(mode="json")
    assert locator.locator_strategy == "bbox"
    assert locator.bbox == {
        "pageIndex": 3,
        "rects": [[1.0, 2.0, 3.0, 4.0]],
    }
    assert len(locator.selected_text or "") == 512
    assert "selected_text_truncated" in locator.warnings
    assert "original_file_path" not in payload
    assert "private_path" not in str(payload)


def test_locator_falls_back_from_text_to_page_without_writes() -> None:
    text_locator = build_fragment_locator(_record(text="exact source text"))
    page_record = _record(text="source text")
    page_record = NotebookFragmentRecord(
        fragment=page_record.fragment.model_copy(update={"text": None}),
        source=page_record.source,
    )
    page_locator = build_fragment_locator(page_record)
    assert text_locator.locator_strategy == "text"
    assert page_locator.locator_strategy == "page"
    assert "precise_highlight_unavailable" in page_locator.warnings


def test_locator_route_has_stable_read_only_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {
        (route.path, method): route
        for route in retrieval_api.router.routes
        for method in getattr(route, "methods", set())
    }
    route = routes[("/api/v1/retrieval/fragments/{fragment_id}/locator", "GET")]
    assert route.response_model is FragmentLocator

    def missing(_value: str) -> FragmentLocator:
        raise FragmentLocatorNotFound("Search fragment locator was not found.")

    monkeypatch.setattr(retrieval_api, "get_fragment_locator", missing)
    with pytest.raises(HTTPException) as raised:
        retrieval_api.fetch_fragment_locator("fixture-missing")
    assert raised.value.status_code == 404
    assert raised.value.detail == {
        "error": "fragment_locator_not_found",
        "message": "Search fragment locator was not found.",
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }

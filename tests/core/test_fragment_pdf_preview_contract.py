from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domains.retrieval.fragment_locator import get_notebook_fragment_locator
from app.services.retrieval.source_registry import RetrievalSourceRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_DATA_ROOT = Path(os.environ.get("NOTEBOOK_AI_LIVE_DATA_ROOT", PROJECT_ROOT))


@pytest.mark.parametrize(
    ("fragment_id", "expected"),
    (
        (
            "2d4d7eac-67ad-5f9c-887c-524fc8519807",
            {"document_id": 1, "pdf_page": 1, "strategy": "text", "rect_count": 0},
        ),
        (
            "00573715-0056-5d28-bc72-1c585a7b700a",
            {"document_id": 10, "pdf_page": 314, "strategy": "bbox", "rect_count": 7},
        ),
        (
            "7ad9bad0-776d-526e-bb8c-658c55ff53af",
            {"document_id": 5, "pdf_page": 13, "strategy": "text", "rect_count": 0},
        ),
    ),
)
def test_live_fragment_locator_characterization_is_read_only(fragment_id: str, expected: dict[str, object]) -> None:
    db_path = LIVE_DATA_ROOT / "data" / "db" / "research_memory.db"
    if not db_path.is_file():
        pytest.skip("production data is not present in this checkout")
    registry = RetrievalSourceRegistry(
        research_db_path=db_path,
        zotero_snapshot_path=LIVE_DATA_ROOT / "data" / "zotero" / "snapshot" / "zotero.sqlite",
        notes_root=LIVE_DATA_ROOT / "data" / "notes",
        project_root=LIVE_DATA_ROOT,
    )
    locator = get_notebook_fragment_locator(fragment_id, registry=registry)
    assert locator.document_id == expected["document_id"]
    assert locator.pdf_page == expected["pdf_page"]
    assert locator.page_index == locator.pdf_page - 1
    assert locator.locator_strategy == expected["strategy"]
    assert len(locator.rects) == expected["rect_count"]
    assert locator.pdf_endpoint == f"/api/v1/library/documents/{locator.document_id}/pdf#page={locator.pdf_page}"
    assert locator.pdf_available is True


def test_document_pdf_endpoint_is_id_only_and_supports_head_and_single_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_api = _pdf_api_or_skip()
    fixture = tmp_path / "registered.pdf"
    fixture.write_bytes(b"%PDF-1.7\nfixture-pdf-content\n")
    monkeypatch.setattr(pdf_api.library_service, "resolve_document_pdf_path", lambda _id: fixture)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(pdf_api.router, prefix="/api/v1/library")
    client = TestClient(app)

    response = client.get("/api/v1/library/documents/1/pdf", headers={"range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == 10

    head = client.head("/api/v1/library/documents/1/pdf")
    assert head.status_code == 200
    assert head.content == b""

    invalid = client.get("/api/v1/library/documents/1/pdf", headers={"range": "bytes=999-1000"})
    assert invalid.status_code == 416


def test_document_pdf_missing_response_never_discloses_resolved_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_api = _pdf_api_or_skip()
    hidden = tmp_path / "private" / "missing.pdf"
    monkeypatch.setattr(pdf_api.library_service, "resolve_document_pdf_path", lambda _id: hidden)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(pdf_api.router, prefix="/api/v1/library")
    response = TestClient(app).get("/api/v1/library/documents/99/pdf")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "document_pdf_not_found"
    assert str(hidden) not in response.text
    assert "resolved_path" not in payload


def test_document_pdf_endpoint_has_no_path_parameter() -> None:
    pdf_api = _pdf_api_or_skip()
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(pdf_api.router, prefix="/api/v1/library")
    response = TestClient(app).get("/api/v1/library/documents/1/pdf?path=C%3A%5Csecret.pdf")
    # The endpoint's route has no path argument; the resolver is deliberately
    # not invoked in this contract check.
    assert response.status_code in {404, 500}
    assert "C:\\secret.pdf" not in response.text


def _pdf_api_or_skip():
    try:
        from app.api.library import pdf as pdf_api
    except ModuleNotFoundError as exc:
        # The clean frontend baseline intentionally excludes the ignored ORM
        # package.  Keep the route tests runnable when that optional local
        # package is present, without fabricating an application module here.
        pytest.skip(f"library PDF route dependencies are unavailable: {exc.name}")
    return pdf_api

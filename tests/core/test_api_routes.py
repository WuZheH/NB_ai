from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_MAJOR_ROUTES = {
    "/health": "GET",
    "/api/v1/system/boundary": "GET",
    "/api/v1/library/read-shelf": "GET",
    "/api/v1/library/search": "GET",
    "/api/v1/library/search/high-quality": "GET",
    "/api/v1/library/documents/{document_id}": "GET",
    "/api/v1/library/books/{document_id}": "GET",
    "/api/v1/library/books/{document_id}/chapters/{chapter_id}/workspace-state": "GET",
    "/api/v1/retrieval/search": "POST",
    "/api/v1/retrieval/index/status": "GET",
    "/api/v1/retrieval/evidence/export": "POST",
    "/api/v1/imports/preview": "POST",
    "/api/v1/zotero/pdf-sources": "GET",
}


def test_major_api_routes_keep_current_urls_and_methods() -> None:
    methods_by_path: dict[str, set[str]] = defaultdict(set)
    for route in app.routes:
        methods_by_path[route.path].update(route.methods or set())

    for path, method in EXPECTED_MAJOR_ROUTES.items():
        assert path in methods_by_path
        assert method in methods_by_path[path]


def test_chapter_review_routes_are_not_part_of_the_product_api() -> None:
    paths = {
        route.path
        for route in app.routes
        if route.path.startswith("/api/v1/library")
    }
    forbidden_fragments = (
        "note-correction",
        "note-classification",
        "object-candidates",
        "relation-candidates",
        "manual-chatgpt-bridge",
        "mechanism-draft-review",
        "zotero-notes/apply",
    )
    for path in paths:
        assert not any(fragment in path for fragment in forbidden_fragments)


def test_product_import_chain_does_not_import_legacy_chapter_routers() -> None:
    main_source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    router_source = (ROOT / "app/api/library/router.py").read_text(encoding="utf-8")
    read_common_source = (ROOT / "app/api/library/read_common.py").read_text(encoding="utf-8")
    assert "from app.api.library.router import router as library_router" in main_source
    for legacy_module in ("chapters", "review", "objects", "mechanisms"):
        assert legacy_module not in router_source
    for legacy_service in (
        "chapter_review_pipeline_service",
        "chapter_note_correction_prompt_service",
        "chapter_workspace_state_service",
    ):
        assert legacy_service not in read_common_source


def test_obsolete_database_search_route_is_not_mounted() -> None:
    paths = {route.path for route in app.routes}
    main_source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "/api/v1/search/database" not in paths
    assert "app.api.search_api" not in main_source
    assert "search_router" not in main_source


def test_manual_preservation_acknowledgment_is_in_deletion_openapi_contract() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    preview = paths[
        "/api/v1/library/documents/{document_id}/deletion-preview"
    ]["post"]
    delete = paths[
        "/api/v1/library/documents/{document_id}/delete"
    ]["post"]
    batch = paths["/api/v1/library/documents/delete-batch"]["post"]
    assert (
        preview["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/DeletionPreviewRequest"
    )
    delete_ref = delete["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    batch_ref = batch["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert delete_ref.endswith(
        "app__schemas__library_deletion__DeleteDocumentRequest"
    )
    assert batch_ref.endswith("DeleteDocumentsBatchRequest")
    delete_schema = schema["components"]["schemas"][
        delete_ref.rsplit("/", 1)[-1]
    ]
    assert "manual_preservation_acknowledgment" in delete_schema["properties"]
    acknowledgment = schema["components"]["schemas"][
        "ManualPreservationAcknowledgment"
    ]
    assert set(acknowledgment["required"]) == {
        "blocker_type",
        "record_ids",
        "document_id",
        "preservation_artifact_directory",
        "preservation_manifest_sha256",
        "acknowledged_by",
        "acknowledgment_text",
    }


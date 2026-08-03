from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "frontend" / "src"
REVIEW_ROOT = SOURCE_ROOT / "features" / "importing" / "review"


def _source(relative_path: str) -> str:
    return (REVIEW_ROOT / relative_path).read_text(encoding="utf-8")


def test_import_review_page_is_a_composition_layer_with_legacy_path() -> None:
    page = (SOURCE_ROOT / "pages" / "ImportReviewPage.jsx").read_text(encoding="utf-8")
    assert len(page.splitlines()) <= 500
    for module in (
        "reviewApi.js",
        "reviewApiClient.js",
        "reviewModel.js",
        "reviewState.js",
        "commitPipeline.js",
        "ReviewInputPanel.jsx",
        "ReviewObjectList.jsx",
        "ImportCommitStatus.jsx",
    ):
        assert module in page
    assert "/api/v1/imports/" not in page
    assert "confirmation_context" not in page
    assert "function updatePhaseEntry" not in page
    assert 'export { default } from "../features/importing/review/ReviewObjectCard.jsx";' == (
        SOURCE_ROOT / "components" / "ReviewObjectCard.jsx"
    ).read_text(encoding="utf-8").strip()


def test_import_review_model_and_state_responsibilities_are_real() -> None:
    model = _source("reviewModel.js")
    for export_name in (
        "normalizeSuggestedObject",
        "normalizeReviewedObject",
        "buildReviewItems",
        "buildReviewedObjectPayload",
        "normalizeTagValue",
        "createEmptyEvidenceRef",
    ):
        assert f"export function {export_name}" in model

    state = _source("reviewState.js")
    for export_name in (
        "toggleReviewStatus",
        "editReviewTag",
        "removeReviewTag",
        "addReviewTag",
        "updateReviewComment",
        "editEvidenceField",
        "removeEvidenceRef",
        "addEvidenceRef",
        "selectEvidenceSection",
        "resetCommitPipeline",
    ):
        assert f"export function {export_name}" in state


def test_import_review_api_and_commit_contracts_are_preserved() -> None:
    api = _source("reviewApi.js")
    for suffix in (
        "source-trace-sections",
        "ai-suggestions",
        "reviewed-objects",
        "remap-reviewed-objects-preview",
        "commit-paper",
        "commit-reviewed-objects",
    ):
        assert f'"{suffix}"' in api
    assert '"commit_paper_after_preview"' in api
    assert '"commit_reviewed_objects_after_remap"' in api
    assert api.count("confirm_write: true") == 2

    pipeline = _source("commitPipeline.js")
    paper_call = pipeline.index("api.commitPaper")
    remap_call = pipeline.index("api.previewReviewedObjectRemap")
    object_call = pipeline.rindex("commitReviewedObjectsPhase")
    assert paper_call < remap_call < object_call
    assert 'failedCount > 0' in pipeline
    assert 'confirmRemapFailed: true' in pipeline
    assert 'status === "already_committed"' in pipeline
    assert "if (onRefresh) onRefresh()" in pipeline


def test_object_review_route_and_feature_exports_remain_available() -> None:
    routes = (SOURCE_ROOT / "app" / "routes.js").read_text(encoding="utf-8")
    assert 'export const OBJECT_REVIEW_PATH = "/object-review";' in routes
    assert 'return { view: "importReview" };' in routes
    feature_entry = (SOURCE_ROOT / "features" / "importing" / "index.js").read_text(
        encoding="utf-8"
    )
    assert 'export { default as ImportReviewPage }' in feature_entry
    assert 'export * from "./review/index.js";' in feature_entry

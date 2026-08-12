from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "frontend" / "src"
EXPECTED_FEATURES = {
    "retrieval": ("LocalRetrievalPage",),
    "library": ("DocumentDetailPage", "EvidenceDetailPage", "ReadShelfPage"),
    "importing": ("ImportPreviewPage", "ImportReviewPage"),
    "workspace": ("ResearchWorkspacePage", "NotebookWorkspaceShell", "FiveLayerSearchResults"),
    "objects": ("ObjectDetailPage",),
    "mechanisms": ("MechanismDraftReviewPanel", "MechanismRelationGraphPanel"),
}


def test_feature_public_entries_exist_and_export_primary_surfaces() -> None:
    for feature, export_names in EXPECTED_FEATURES.items():
        entry = SOURCE_ROOT / "features" / feature / "index.js"
        assert entry.is_file()
        source = entry.read_text(encoding="utf-8")
        for export_name in export_names:
            assert f" as {export_name} " in source


def test_canonical_app_composes_feature_entries() -> None:
    source = (SOURCE_ROOT / "app" / "App.jsx").read_text(encoding="utf-8")
    for feature in ("retrieval", "library", "importing", "workspace", "objects"):
        assert f'../features/{feature}/index.js"' in source
    assert '../features/search/index.js"' not in source


def test_import_preview_uses_feature_formatter_module() -> None:
    formatter_path = SOURCE_ROOT / "features" / "importing" / "utils" / "importPreviewFormatters.js"
    content_path = SOURCE_ROOT / "features" / "importing" / "components" / "ImportPreviewContent.jsx"
    formatter_source = formatter_path.read_text(encoding="utf-8")
    legacy_page = (SOURCE_ROOT / "pages" / "ImportPreviewPage.jsx").read_text(encoding="utf-8")
    content_source = content_path.read_text(encoding="utf-8")
    consumers = legacy_page + content_source
    for export_name in (
        "decisionMessage",
        "qualityStatusLabel",
        "deviceLabel",
        "importJobStatusLabel",
        "cacheStatusLabel",
        "nativeNotesSummary",
        "documentTypeLabel",
        "importModeLabel",
        "confidenceLabel",
        "stageLabel",
        "basename",
    ):
        assert f"export function {export_name}" in formatter_source
        assert export_name in consumers
    assert "importPreviewFormatters.js" in legacy_page
    assert "importPreviewFormatters.js" in content_source


def test_import_preview_presentation_tail_is_split_behind_named_imports() -> None:
    legacy_page = (SOURCE_ROOT / "pages" / "ImportPreviewPage.jsx").read_text(encoding="utf-8")
    content_source = (
        SOURCE_ROOT / "features" / "importing" / "components" / "ImportPreviewContent.jsx"
    ).read_text(encoding="utf-8")
    assert len(legacy_page.splitlines()) < 2_000
    assert 'from "../features/importing/components/ImportPreviewContent.jsx"' in legacy_page
    assert "function ImportLinearWizard" not in legacy_page
    for export_name in (
        "ImportLinearWizard",
        "PreviewWorkspace",
        "buildImportReadiness",
        "evaluateConvertedMdIdentity",
        "resetForNewImportSource",
    ):
        assert f"export function {export_name}" in content_source
        assert export_name in legacy_page


def test_five_layer_results_use_retrieval_feature_sections_and_utils() -> None:
    legacy_path = SOURCE_ROOT / "components" / "workspace" / "FiveLayerSearchResults.jsx"
    sections_path = SOURCE_ROOT / "features" / "retrieval" / "components" / "RetrievalResultSections.jsx"
    utils_path = SOURCE_ROOT / "features" / "retrieval" / "utils" / "retrievalResults.js"
    legacy_source = legacy_path.read_text(encoding="utf-8")
    sections_source = sections_path.read_text(encoding="utf-8")
    utils_source = utils_path.read_text(encoding="utf-8")
    assert len(legacy_source.splitlines()) < 350
    assert "export default function FiveLayerSearchResults" in legacy_source
    assert 'import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";' in legacy_source
    assert "function ResearchEvidencePacketPanel" not in legacy_source
    assert "RetrievalResultSections.jsx" in legacy_source
    assert "retrievalResults.js" in legacy_source
    for export_name in (
        "ResearchEvidencePacketPanel",
        "StructuredRetrievalOverview",
        "StructuredResultSection",
        "ApprovedObjectCandidatesSection",
        "ResearchGateSummary",
        "SearchLayerSection",
        "SearchResultCard",
        "ResultEvidenceFields",
        "SearchEmptyGateCard",
    ):
        assert f"export function {export_name}" in sections_source
    for export_name in (
        "normalizePacketResults",
        "buildPacketQualitySummary",
        "buildEvidencePacketText",
        "buildEvidencePacketJson",
        "downloadTextFile",
        "packetFilename",
        "sourceTargetFromResult",
        "gateReasonLabel",
    ):
        assert f"export function {export_name}" in utils_source
    assert "related_keywords: relatedKeywords" in utils_source


def test_library_entry_uses_read_only_document_and_chapter_details() -> None:
    entry_path = SOURCE_ROOT / "features" / "library" / "index.js"
    document_path = SOURCE_ROOT / "pages" / "DocumentDetailPage.jsx"
    legacy_path = SOURCE_ROOT / "pages" / "BookDetailPage.jsx"
    content_path = SOURCE_ROOT / "features" / "library" / "components" / "BookDetailContent.jsx"
    entry_source = entry_path.read_text(encoding="utf-8")
    document_source = document_path.read_text(encoding="utf-8")
    assert "BookDetailPage" not in entry_source
    assert "BookDetailContent" not in entry_source
    assert "NoteCorrectionReviewWorkbench" not in entry_source
    assert "ReadOnlyChapterList" in document_source
    assert "BookDetailPage" not in document_source
    assert "noteFirstWorkflow" not in document_source
    assert legacy_path.is_file()
    assert content_path.is_file()


def test_research_workspace_uses_generic_empty_state_and_library_sources() -> None:
    legacy_path = SOURCE_ROOT / "pages" / "ResearchWorkspacePage.jsx"
    home_path = SOURCE_ROOT / "features" / "workspace" / "components" / "ResearchWorkspaceHome.jsx"
    utils_path = SOURCE_ROOT / "features" / "workspace" / "utils" / "researchWorkspace.js"
    legacy_source = legacy_path.read_text(encoding="utf-8")
    home_source = home_path.read_text(encoding="utf-8")
    utils_source = utils_path.read_text(encoding="utf-8")
    assert len(legacy_source.splitlines()) < 275
    assert "export default function ResearchWorkspacePage" in legacy_source
    assert "export { buildEmptyWorkspaceState };" in legacy_source
    assert "function buildEmptyWorkspaceState" not in legacy_source
    assert "ResearchWorkspaceHome.jsx" in legacy_source
    assert "researchWorkspace.js" in legacy_source
    assert "export function NotebookWorkspaceHome" in home_source
    assert "export function NotebookCard" in home_source
    for export_name in (
        "buildEmptyWorkspaceState",
        "buildWorkspaceNotebooks",
        "loadWorkspaceHome",
        "openSourceWorkspace",
    ):
        assert f"function {export_name}" in utils_source
    assert '"/api/v1/library/read-shelf"' in utils_source
    combined = "\n".join((legacy_source, home_source, utils_source))
    for forbidden in (
        "buildDeterministicWorkspaceFallbackState",
        "DEFAULT_HOME_WORKFLOW_TARGET",
        "MACHINE_LEARNING_NOTEBOOK",
        "Probabilistic machine learning",
        "section_8_",
        "SYNPN068",
        "note-correction",
        "AdvancedWorkflowDrawer",
        "MechanismRelationGraphPanel",
    ):
        assert forbidden not in combined


def test_document_detail_uses_library_feature_note_helpers() -> None:
    legacy_source = (SOURCE_ROOT / "pages" / "DocumentDetailPage.jsx").read_text(encoding="utf-8")
    utils_source = (
        SOURCE_ROOT / "features" / "library" / "utils" / "documentDetail.js"
    ).read_text(encoding="utf-8")
    assert "features/library/utils/documentDetail.js" in legacy_source
    assert "function noteTypeTags" not in legacy_source
    for export_name in (
        "noteTypeTags",
        "noteSort",
        "notesSourceSummary",
        "noteProcessingSummary",
        "noteMatchedChunkIds",
    ):
        assert f"export function {export_name}" in utils_source


def test_legacy_component_and_page_paths_remain_available() -> None:
    legacy_paths = (
        "pages/ImportPreviewPage.jsx",
        "pages/ImportReviewPage.jsx",
        "pages/BookDetailPage.jsx",
        "pages/DocumentDetailPage.jsx",
        "pages/ResearchWorkspacePage.jsx",
        "components/book/ChapterNoteCorrectionPanel.jsx",
        "components/workspace/FiveLayerSearchResults.jsx",
    )
    for relative_path in legacy_paths:
        assert (SOURCE_ROOT / relative_path).is_file()


def test_workspace_css_facade_preserves_chunk_order() -> None:
    facade = (SOURCE_ROOT / "styles" / "workspace.css").read_text(encoding="utf-8")
    imports = [line.strip() for line in facade.splitlines() if line.strip()]
    assert imports == [
        '@import "../features/workspace/styles/shell.css";',
        '@import "../features/workspace/styles/visual-language.css";',
        '@import "../features/workspace/styles/layout-polish.css";',
        '@import "../features/workspace/styles/reference-alignment.css";',
    ]

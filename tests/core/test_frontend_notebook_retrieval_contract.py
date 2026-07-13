from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SOURCE = PROJECT_ROOT / "frontend" / "src"


def _source(relative_path: str) -> str:
    return (FRONTEND_SOURCE / relative_path).read_text(encoding="utf-8")


def test_retrieval_api_preserves_fts_and_adds_notebook_read_endpoints() -> None:
    source = _source("services/retrievalApi.js")
    assert 'RETRIEVAL_SEARCH_ENDPOINT = "/api/v1/retrieval/search"' in source
    assert 'NOTEBOOK_SEARCH_ENDPOINT = "/api/v1/retrieval/notebook-search"' in source
    assert 'RETRIEVAL_FRAGMENT_ENDPOINT = "/api/v1/retrieval/fragments"' in source
    assert 'RETRIEVAL_EXPORT_ENDPOINT = "/api/v1/retrieval/evidence/export"' in source
    assert "postJson(NOTEBOOK_SEARCH_ENDPOINT, request" in source
    assert "getJson(`${RETRIEVAL_FRAGMENT_ENDPOINT}/${encodeURIComponent(normalizedId)}`" in source
    assert "fetch(" not in source


def test_notebook_search_defaults_and_source_boundary_are_explicit() -> None:
    page = _source("pages/LocalRetrievalPage.jsx")
    helpers = _source("features/retrieval/utils/notebookSearch.js")
    assert "useState(HIGH_QUALITY_SEARCH_KIND)" in page
    assert "useState(\"precision\")" in page
    assert "useState(12)" in page
    for source_type in (
        "pdf_chunk",
        "zotero_annotation_comment",
        "zotero_child_note",
        "zotero_inspiration_note",
    ):
        assert f'  "{source_type}",' in helpers
    approved_block = helpers.split("export const NOTEBOOK_SOURCE_TYPES", 1)[1].split("]);", 1)[0]
    for excluded in ("zotero_highlight", "personal_note", "markdown_note"):
        assert excluded not in approved_block
    assert "Math.min(50" in helpers
    assert "source_types: sourceTypes" in helpers
    assert "document_ids: documentId ? [documentId] : []" in helpers


def test_high_quality_export_does_not_send_legacy_fts_mode() -> None:
    page = _source("pages/LocalRetrievalPage.jsx")
    assert "lastSearchRequest?.kind === KEYWORD_SEARCH_KIND" in page
    assert "exportRequest.retrieval_mode = lastSearchRequest.request.mode || ftsMode" in page
    assert "retrieval_mode:" not in page


def test_result_card_keeps_evidence_roles_and_open_target_visible() -> None:
    card = _source("components/retrieval/RetrievalResultCard.jsx")
    for label in (
        "PDF 原文",
        "用户笔记",
        "对应选中文本",
        "展开上下文",
        "查看 provenance",
        "复制单条",
        "读取完整片段",
        "打开 PDF 页",
        "打开 Zotero 条目",
    ):
        assert label in card
    assert "displayResult.note_text" in card
    assert "displayResult.selected_text" in card
    assert "openTargetActions(displayResult, API_BASE_URL)" in card
    assert "localRetrievalFragmentId" in card
    assert "displayResult.reranker_score" in card
    assert "displayResult.semantic_score" in card


def test_retrieval_route_facade_and_mobile_styles_remain() -> None:
    feature_entry = _source("features/retrieval/index.js")
    styles = _source("styles/retrieval.css")
    routes = _source("app/routes.js")
    assert ' as LocalRetrievalPage ' in feature_entry
    assert 'LOCAL_RETRIEVAL_PATH = "/retrieval"' in routes
    assert "@media (max-width: 900px)" in styles
    assert "@media (max-width: 620px)" in styles
    assert ".localRetrievalEvidenceBlock.userNote" in styles
    assert ".localRetrievalEvidenceBlock.selectedText" in styles

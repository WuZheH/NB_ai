from __future__ import annotations

import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_BRAND = re.compile(r"notebook(?:[_ -]?ai)|notebookai", re.IGNORECASE)


def test_frontend_user_facing_sources_use_search_brand() -> None:
    frontend_root = PROJECT_ROOT / "frontend" / "src"
    allowed_internal_values = (
        "notes_imported_or_existing_in_notebook_ai",
        "import_zotero_notes_to_notebook_ai",
    )
    violations: list[str] = []
    for path in sorted(frontend_root.rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx", ".css"}:
            continue
        source = path.read_text(encoding="utf-8")
        for value in allowed_internal_values:
            source = source.replace(value, "internal-compatibility-value")
        if FORBIDDEN_BRAND.search(source):
            violations.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert violations == []


def test_desktop_product_metadata_and_visible_resources_use_search() -> None:
    desktop_root = PROJECT_ROOT / "integrations" / "search_desktop"
    package = json.loads((desktop_root / "package.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        (desktop_root / "electron" / "product-metadata.json").read_text(encoding="utf-8")
    )
    assert package["productName"] == "Search"
    assert package["build"]["productName"] == "Search"
    assert metadata["productName"] == "Search"
    assert metadata["identityResource"] == "package.json#searchBuildIdentity"
    assert "buildId" not in metadata
    for relative_path in (
        "renderer/missing-build.html",
        "renderer/desktop-shell.html",
        "renderer/desktop-shell.js",
        "electron/main/window.js",
        "electron/main/startupLogger.js",
        "electron/tray/createTray.js",
    ):
        source = (desktop_root / relative_path).read_text(encoding="utf-8")
        assert not FORBIDDEN_BRAND.search(source), relative_path


def test_zotero_visible_labels_use_search_while_ids_remain_compatible() -> None:
    plugin_root = PROJECT_ROOT / "zotero-plugin"
    manifest = json.loads((plugin_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Search Inspiration"
    assert manifest["description"] == "Capture Zotero reading inspirations for Search."
    assert manifest["applications"]["zotero"]["id"] == (
        "notebook-ai-inspiration@notebook-ai.local"
    )
    visible_sources = "\n".join(
        (plugin_root / path).read_text(encoding="utf-8")
        for path in (
            "bootstrap.js",
            "src/inspirationSidebar.js",
            "src/zoteroReaderBridge.js",
        )
    )
    for old_display in (
        "Notebook AI Inspiration",
        "NOTEBOOK_AI Markdown",
        "NOTEBOOK_AI Current PDF",
        "NOTEBOOK_AI Selected PDF",
        "[NOTEBOOK_AI Inspiration",
    ):
        assert old_display not in visible_sources


def test_generated_user_content_uses_search_brand() -> None:
    for relative_path in (
        "app/domains/retrieval/evidence_export_adapter.py",
        "app/domains/retrieval/fragment_repository.py",
        "app/services/retrieval/evidence_export_service.py",
        "app/services/mechanism_prompt_export_service.py",
        "app/services/zotero_markdown_export_service.py",
    ):
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert not FORBIDDEN_BRAND.search(source), relative_path

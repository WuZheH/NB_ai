from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = PROJECT_ROOT / "zotero-plugin"
MAIN_SOURCE_FILES = (
    "src/inspirationQuickNote.js",
    "src/inspirationSidebar.js",
    "src/inspirationStore.js",
    "src/syncClient.js",
    "src/zoteroReaderBridge.js",
)


def test_zotero_plugin_files_and_manifest_contract_exist() -> None:
    manifest_path = PLUGIN_ROOT / "manifest.json"
    bootstrap_path = PLUGIN_ROOT / "bootstrap.js"
    assert manifest_path.is_file()
    assert bootstrap_path.is_file()
    for relative_path in MAIN_SOURCE_FILES:
        assert (PLUGIN_ROOT / relative_path).is_file(), relative_path

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "Notebook AI Inspiration"
    assert manifest["applications"]["zotero"]["id"] == "notebook-ai-inspiration@notebook-ai.local"


def test_zotero_public_entry_and_packaging_script_remain_available() -> None:
    bootstrap_source = (PLUGIN_ROOT / "bootstrap.js").read_text(encoding="utf-8")
    for lifecycle_entry in ("startup", "shutdown", "install", "uninstall"):
        assert f"function {lifecycle_entry}(" in bootstrap_source
    assert "Zotero.NotebookAIInspirationPlugin" in bootstrap_source
    assert "NOTEBOOK_AI_INSPIRATION_PLUGIN" in bootstrap_source

    packaging_script = PROJECT_ROOT / "scripts" / "package_zotero_inspiration_plugin.py"
    assert packaging_script.is_file()
    packaging_source = packaging_script.read_text(encoding="utf-8")
    assert "def package_plugin(" in packaging_source
    assert "zotero-plugin" in packaging_source


from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = PROJECT_ROOT / "integrations" / "notebook_ai_chatgpt_app"
PLUGIN_ROOT = PROJECT_ROOT / "integrations" / "plugins" / "search"


def test_read_is_the_formal_user_visible_product_identity() -> None:
    identity = (MCP_ROOT / "server" / "productIdentity.ts").read_text(
        encoding="utf-8"
    )
    widget = (MCP_ROOT / "server" / "widgetResource.ts").read_text(
        encoding="utf-8"
    )
    readme = (MCP_ROOT / "README.md").read_text(encoding="utf-8")
    plugin = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )

    assert 'READ_PRODUCT_NAME = "READ"' in identity
    assert "READ searches and imports the user's own reading library." in identity
    assert "use search before answering" in identity
    assert "explicitly confirms" in identity
    assert "never retry it automatically" in identity
    assert "selected_source_text" in identity
    assert "user_note" in identity
    assert 'WIDGET_DOMAIN = "https://read-library-widget.openaiusercontent.com"' in widget
    assert plugin["name"] == "read"
    assert plugin["interface"]["displayName"] == "READ"
    assert readme.startswith("# READ for ChatGPT")
    assert "Name it **READ**" in readme

    exposed_sources = "\n".join(
        [
            identity,
            widget,
            readme,
            (MCP_ROOT / ".env.example").read_text(encoding="utf-8"),
            json.dumps(plugin, ensure_ascii=False),
            (
                PLUGIN_ROOT
                / "skills"
                / "use-search-research"
                / "agents"
                / "openai.yaml"
            ).read_text(encoding="utf-8"),
            (
                PLUGIN_ROOT / "skills" / "use-search-research" / "SKILL.md"
            ).read_text(encoding="utf-8"),
        ]
    ).lower()
    assert "cread" not in exposed_sources
    assert "cread secure" not in exposed_sources
    assert "翻书" not in exposed_sources


def test_read_keeps_the_stable_ten_tool_surface() -> None:
    tools = (MCP_ROOT / "server" / "tools" / "index.ts").read_text(
        encoding="utf-8"
    )
    expected = {
        "search",
        "fetch",
        "export_evidence",
        "list_library",
        "integrity_report",
        "import_preview",
        "import_document",
        "import_status",
        "delete_preview",
        "delete_document",
    }
    for name in expected:
        assert f'"{name}"' in tools
    assert tools.count('  "') == len(expected)


def test_read_import_instructions_require_preview_confirmation_and_status_recovery() -> None:
    preview = (MCP_ROOT / "server" / "tools" / "importPreview.ts").read_text(
        encoding="utf-8"
    )
    commit = (MCP_ROOT / "server" / "tools" / "importDocument.ts").read_text(
        encoding="utf-8"
    )
    status = (MCP_ROOT / "server" / "tools" / "importStatus.ts").read_text(
        encoding="utf-8"
    )

    assert "annotation_comment_count" in preview
    assert "Never call import_document from this preview alone" in preview
    assert "explicit user confirmation" in commit
    assert "Call at most once" in commit
    assert "do not retry" in commit
    assert "operation_id with import_status" in commit
    assert "This never retries an import" in status

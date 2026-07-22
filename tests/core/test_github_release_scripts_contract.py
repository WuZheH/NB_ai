from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_windows_release_scripts_are_portable_and_non_system_mutating() -> None:
    paths = (
        "scripts/bootstrap_windows.ps1",
        "scripts/start_dev.ps1",
        "scripts/test_all.ps1",
        "scripts/build_windows.ps1",
        "scripts/start_quick_tunnel.ps1",
    )
    combined = "\n".join(_source(path) for path in paths)
    forbidden = (
        "D:" + "\\LEARNING",
        "D:" + "/LEARNING",
        "C:" + "\\Users\\",
        "setx ",
        "reg add",
        "reg delete",
        "New-ItemProperty",
        "Set-ItemProperty",
        "[Environment]::SetEnvironmentVariable",
        "winget ",
        "choco ",
    )
    for value in forbidden:
        assert value.casefold() not in combined.casefold()


def test_bootstrap_defaults_to_check_and_gates_dependency_installation() -> None:
    source = _source("scripts/bootstrap_windows.ps1")
    assert "[switch]$Install" in source
    assert "[switch]$CheckOnly" in source
    assert "search_install_requires_project_venv_or_active_non_base_conda" in source
    assert "pip install --cache-dir $PipCache -r $LockFile" in source
    assert " ci `" in source
    assert "--no-audit --no-fund" in source
    assert ".codex_tmp\\bootstrap" in source


def test_test_and_build_scripts_keep_outputs_inside_unique_project_directories() -> None:
    tests = _source("scripts/test_all.ps1")
    build = _source("scripts/build_windows.ps1")
    assert ".codex_tmp\\test-all" in tests
    assert "SEARCH_ELECTRON_TEST_MODE" in tests
    assert "ELECTRON_DISABLE_CRASH_REPORTING" in tests
    assert "[string]$BuildId" in build
    assert "[string]$OutputRoot" in build
    assert "status --porcelain --untracked-files=normal" in build
    assert "rev-parse HEAD" in build
    assert "symbolic-ref --short -q HEAD" in build
    assert "--config.directories.output=$OutputRoot" in build
    assert "searchBuildIdentity.source_commit" in build
    assert "search-build-report.json" in build
    assert "[string]$SourceCommit" not in build
    assert "search_candidate_output_already_exists" in build
    assert "current_formal_package_untouched = $true" in build
    assert "complete_tree_sha256" in build
    assert "resources_app_sha256" in build
    assert "Get-SearchTreeHash" in build
    assert "tree_hash_schema" in build
    tree_hash = _source("scripts/lib/search_tree_hash.ps1")
    assert "OrdinalIgnoreCase" in tree_hash
    assert "Sort-Object" not in tree_hash
    assert 'empty_directories = "excluded"' in tree_hash
    assert build.count("Remove-Item") == 1
    assert '$InvalidExecutable = Join-Path $PackagedRoot "Search.exe"' in build
    assert "Remove-Item -LiteralPath $InvalidExecutable -Force -ErrorAction Stop" in build


def test_environment_examples_contain_no_credentials_or_machine_paths() -> None:
    source = _source(".env.example")
    for name in (
        "SEARCH_DATA_DIR",
        "SEARCH_PYTHON",
        "SEARCH_NODE",
        "SEARCH_CLOUDFLARED",
        "SEARCH_TUNNEL_STATE_DIR",
        "SEARCH_BACKEND_PORT",
        "SEARCH_MCP_PORT",
        "SEARCH_LOG_DIR",
        "SEARCH_RUNTIME_DIR",
    ):
        assert f"{name}=" in source
    assert "D:\\" not in source
    assert re.search(r"(?m)^SEARCH_BACKEND_BEARER_TOKEN=\s*$", source)
    assert "password=" not in source.casefold()


def test_import_preview_defers_model_cache_resolution_to_the_backend() -> None:
    source = _source("frontend/src/pages/ImportPreviewPage.jsx")
    request = source.split('postJson("/api/v1/library/import/pdf/repair-preview/start"', 1)[1]
    request = request.split("});", 1)[0]
    assert "model_cache_root" not in request

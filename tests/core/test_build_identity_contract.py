from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.build_identity import BuildIdentity, load_runtime_build_identity
from app.runtime.config import RuntimeConfig
from app.runtime.supervisor import RuntimeController


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _identity() -> dict[str, str]:
    return {
        "schema_version": "search.build-identity.v1",
        "build_mode": "packaged",
        "product": "Search",
        "version": "0.1.4",
        "build_id": "test-search-candidate",
        "source_commit": COMMIT,
        "source_branch": "codex/test-build-identity",
        "build_timestamp_utc": "2026-07-19T00:00:00.000Z",
    }


def _packaged_runtime(tmp_path: Path, *, include_identity: bool = True) -> tuple[Path, Path]:
    app_root = tmp_path / "package" / "resources" / "app"
    runtime_root = app_root / "runtime-project"
    runtime_root.mkdir(parents=True)
    package = {"productName": "Search", "version": "0.1.4"}
    if include_identity:
        package["searchBuildIdentity"] = _identity()
    package_path = app_root / "package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    return runtime_root, package_path


def test_packaged_identity_is_loaded_from_the_single_package_resource(tmp_path: Path) -> None:
    runtime_root, package_path = _packaged_runtime(tmp_path)
    identity = load_runtime_build_identity(
        runtime_root=runtime_root,
        env={
            "SEARCH_BUILD_MODE": "packaged",
            "SEARCH_BUILD_IDENTITY_PATH": str(package_path),
        },
    )
    assert identity == BuildIdentity.from_dict(_identity(), expected_mode="packaged")


def test_packaged_mode_fails_when_identity_metadata_is_missing(tmp_path: Path) -> None:
    runtime_root, package_path = _packaged_runtime(tmp_path, include_identity=False)
    with pytest.raises(ValueError, match="search_packaged_build_identity_missing"):
        load_runtime_build_identity(
            runtime_root=runtime_root,
            env={
                "SEARCH_BUILD_MODE": "packaged",
                "SEARCH_BUILD_IDENTITY_PATH": str(package_path),
            },
        )


def test_packaged_identity_rejects_forged_commit_and_development_build_id() -> None:
    with pytest.raises(ValueError, match="search_packaged_build_identity_invalid"):
        BuildIdentity.from_dict({**_identity(), "source_commit": "forged"})
    with pytest.raises(ValueError, match="search_packaged_build_identity_invalid"):
        BuildIdentity.from_dict({**_identity(), "build_id": "development"})


def test_runtime_status_uses_identity_and_the_effective_data_directory(tmp_path: Path) -> None:
    runtime_root, package_path = _packaged_runtime(tmp_path)
    data_dir = tmp_path / "canonical-data"
    config = RuntimeConfig.load(
        runtime_root=runtime_root,
        data_dir=data_dir,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "APPDATA": str(tmp_path / "roaming"),
            "SEARCH_BUILD_MODE": "packaged",
            "SEARCH_BUILD_IDENTITY_PATH": str(package_path),
            "SEARCH_PYTHON": str(tmp_path / "python.exe"),
            "SEARCH_NODE": str(tmp_path / "node.exe"),
        },
    )
    status = RuntimeController(config).status().to_dict()
    assert status["product"] == "Search"
    assert status["version"] == "0.1.4"
    assert status["build_id"] == "test-search-candidate"
    assert status["source_commit"] == COMMIT
    assert status["source_branch"] == "codex/test-build-identity"
    assert status["data_root"] == str(data_dir.resolve())


def test_formal_build_entry_has_no_source_commit_override_and_no_fixed_candidate_path() -> None:
    source = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "[string]$BuildId" in source
    assert "[string]$OutputRoot" in source
    assert "[string]$SourceCommit" not in source
    assert "rev-parse HEAD" in source
    assert "search_build_requires_clean_worktree" in source
    assert "search_candidate_output_already_exists" in source
    assert "--config.directories.output=$OutputRoot" in source
    assert "dist\\win-unpacked" not in source
    assert "ConvertTo-SearchIdentityString" in source
    assert "ToUniversalTime().ToString" in source
    assert 'Join-Path $PackagedRoot "Search.exe"' in source
    assert "Remove-Item -LiteralPath $InvalidExecutable" in source

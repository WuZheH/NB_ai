from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.core import sandbox_support
from tests.core.sandbox_support import (
    SENTINEL_NAME,
    _is_owned_sandbox,
    _reject_unsafe_parent,
    owned_sandbox_root,
    purge_owned_sandboxes,
    sandbox_parent,
)


def test_parent_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", "relative/path")
    with pytest.raises(RuntimeError, match="absolute"):
        sandbox_parent()


def test_parent_rejects_drive_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", "C:\\")
    with pytest.raises(RuntimeError, match="drive root"):
        sandbox_parent()


def test_parent_rejects_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", str(Path.home()))
    with pytest.raises(RuntimeError, match="home"):
        sandbox_parent()


def test_parent_rejects_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", str(repo_root))
    with pytest.raises(RuntimeError, match="repository root"):
        sandbox_parent()


def test_parent_rejects_production_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.paths import DATA_DIR

    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", str(DATA_DIR))
    with pytest.raises(RuntimeError, match="production data"):
        sandbox_parent()


def test_owned_sandbox_has_sentinel_and_unique_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", str(tmp_path))
    first = owned_sandbox_root()
    second = owned_sandbox_root()
    assert first != second
    assert first.parent == tmp_path.resolve()
    assert first.name.startswith("search-core-test-")
    assert (first / SENTINEL_NAME).is_file()
    assert _is_owned_sandbox(first)
    assert _is_owned_sandbox(second)


def test_purge_removes_only_owned_sandboxes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEARCH_TEST_DATA_ROOT", str(tmp_path))
    owned = owned_sandbox_root()
    unowned = tmp_path / "search-core-test-unowned"
    unowned.mkdir()
    (unowned / "valuable.txt").write_text("keep me", encoding="utf-8")
    other = tmp_path / "unrelated-dir"
    other.mkdir()
    (other / "keep.txt").write_text("keep me too", encoding="utf-8")

    purge_owned_sandboxes()

    assert not owned.exists()
    assert unowned.is_dir()
    assert (unowned / "valuable.txt").is_file()
    assert other.is_dir()
    assert (other / "keep.txt").is_file()
    assert tmp_path.is_dir()


def test_reject_unsafe_parent_direct() -> None:
    with pytest.raises(RuntimeError):
        _reject_unsafe_parent(Path("C:\\"))
    with pytest.raises(RuntimeError):
        _reject_unsafe_parent(Path.home())
    with pytest.raises(RuntimeError):
        _reject_unsafe_parent(Path(__file__).resolve().parents[2])
    from app.core.paths import DATA_DIR

    with pytest.raises(RuntimeError):
        _reject_unsafe_parent(Path(DATA_DIR))


def test_sandbox_redirect_happens_before_first_app_import(
    tmp_path: Path,
) -> None:
    """Fresh-process proof that conftest redirects SEARCH_DATA_DIR first.

    With only SEARCH_TEST_DATA_ROOT set, importing the conftest must cause
    app.core.paths to resolve DATA_DIR into the newly created sandbox child,
    never the ambient/repository data directory.
    """
    repo_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("SEARCH_DATA_DIR", None)
    environment["SEARCH_TEST_DATA_ROOT"] = str(tmp_path)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    code = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import tests.core.conftest as conftest\n"
        "from app.core.paths import DATA_DIR\n"
        "print(json.dumps({\n"
        "    'data_dir': str(Path(DATA_DIR).resolve()),\n"
        "    'sandbox': conftest.SANDBOX,\n"
        "    'test_root': os.environ['SEARCH_TEST_DATA_ROOT'],\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, str(repo_root)],
        cwd=str(repo_root),
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    data_dir = Path(payload["data_dir"])
    sandbox = Path(payload["sandbox"])
    assert data_dir == sandbox
    assert data_dir.parent == Path(payload["test_root"]).resolve()
    assert data_dir.name.startswith("search-core-test-")
    assert data_dir != (repo_root / "data").resolve()
    assert (data_dir / SENTINEL_NAME).is_file()

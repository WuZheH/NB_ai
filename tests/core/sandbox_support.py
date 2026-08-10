from __future__ import annotations

"""Safe sandbox location helpers for the core test suite.

``SEARCH_TEST_DATA_ROOT`` is only ever interpreted as a *parent* directory.
The sandbox itself is a uniquely named child created with ``mkdtemp`` and
guarded by an ownership sentinel; nothing outside that owned child is ever
deleted.  A set of hard guards rejects drive roots, the user home, the
repository root, and the production data directory as parents.
"""

import os
import tempfile
from pathlib import Path

SENTINEL_NAME = ".search-core-test-sandbox-owner"


def _resolve_configured_parent() -> Path | None:
    configured = os.environ.get("SEARCH_TEST_DATA_ROOT", "").strip()
    if not configured or "\x00" in configured:
        return None
    raw = Path(configured).expanduser()
    if not raw.is_absolute():
        raise RuntimeError("SEARCH_TEST_DATA_ROOT must be an absolute path")
    try:
        return raw.resolve(strict=False)
    except OSError:
        return None


def _production_data_dir() -> Path | None:
    try:
        from app.core.paths import DATA_DIR

        return Path(DATA_DIR).resolve(strict=False)
    except Exception:
        return None


def _reject_unsafe_parent(parent: Path) -> None:
    if not parent.is_absolute():
        raise RuntimeError("SEARCH_TEST_DATA_ROOT must be an absolute path")
    anchor = Path(parent.anchor)
    if parent == anchor:
        raise RuntimeError(
            "SEARCH_TEST_DATA_ROOT must not be a drive root"
        )
    home = Path.home().resolve(strict=False)
    if parent == home:
        raise RuntimeError(
            "SEARCH_TEST_DATA_ROOT must not be the user home directory"
        )
    try:
        repo_root = Path(__file__).resolve().parents[2]
    except OSError:
        repo_root = None
    if repo_root is not None and parent == repo_root:
        raise RuntimeError(
            "SEARCH_TEST_DATA_ROOT must not be the repository root"
        )
    production = _production_data_dir()
    if production is not None and parent == production:
        raise RuntimeError(
            "SEARCH_TEST_DATA_ROOT must not be the production data directory"
        )
    if production is not None and production in parent.parents:
        raise RuntimeError(
            "SEARCH_TEST_DATA_ROOT must not contain the production data directory"
        )


def sandbox_parent() -> Path:
    configured = _resolve_configured_parent()
    if configured is not None:
        _reject_unsafe_parent(configured)
        try:
            configured.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                "SEARCH_TEST_DATA_ROOT cannot be created"
            ) from exc
        return configured
    return Path(tempfile.gettempdir()).resolve(strict=False)


def owned_sandbox_root() -> Path:
    parent = sandbox_parent()
    root = Path(
        tempfile.mkdtemp(
            prefix="search-core-test-",
            dir=str(parent),
        )
    )
    (root / SENTINEL_NAME).write_text(
        "owned by tests/core/conftest.py\n",
        encoding="utf-8",
    )
    return root


def _is_owned_sandbox(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not path.name.startswith("search-core-test-"):
        return False
    try:
        return (path / SENTINEL_NAME).is_file()
    except OSError:
        return False


def purge_owned_sandboxes() -> None:
    """Remove only previously owned sandbox children of the parent."""
    parent = sandbox_parent()
    try:
        candidates = sorted(parent.iterdir())
    except OSError:
        return
    for entry in candidates:
        if _is_owned_sandbox(entry):
            try:
                import shutil

                shutil.rmtree(entry, ignore_errors=True)
            except Exception:
                continue

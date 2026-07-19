from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from app.core.paths import RUNTIME_PROJECT_ROOT


BUILD_IDENTITY_SCHEMA_VERSION = "search.build-identity.v1"
BUILD_IDENTITY_PROPERTY = "searchBuildIdentity"
DEVELOPMENT_BUILD_ID = "development"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_BUILD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class BuildIdentity:
    schema_version: str
    build_mode: str
    product: str
    version: str
    build_id: str
    source_commit: str
    source_branch: str
    build_timestamp_utc: str

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_version: str | None = None,
        expected_mode: str | None = None,
    ) -> "BuildIdentity":
        try:
            identity = cls(
                schema_version=_clean(value["schema_version"]),
                build_mode=_clean(value["build_mode"]),
                product=_clean(value["product"]),
                version=_clean(value["version"]),
                build_id=_clean(value["build_id"]),
                source_commit=_clean(value["source_commit"]).lower(),
                source_branch=_clean(value["source_branch"]),
                build_timestamp_utc=_clean(value["build_timestamp_utc"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("search_build_identity_invalid") from exc
        identity.validate(expected_version=expected_version, expected_mode=expected_mode)
        return identity

    def validate(
        self,
        *,
        expected_version: str | None = None,
        expected_mode: str | None = None,
    ) -> None:
        if (
            self.schema_version != BUILD_IDENTITY_SCHEMA_VERSION
            or self.product != "Search"
            or not _VERSION_PATTERN.fullmatch(self.version)
            or self.build_mode not in {"development", "packaged"}
            or not self.source_branch
            or not _valid_utc_timestamp(self.build_timestamp_utc)
        ):
            raise ValueError("search_build_identity_invalid")
        if expected_version and self.version != expected_version:
            raise ValueError("search_build_identity_version_mismatch")
        if expected_mode and self.build_mode != expected_mode:
            raise ValueError("search_build_identity_mode_mismatch")
        if self.build_mode == "packaged":
            if (
                self.build_id == DEVELOPMENT_BUILD_ID
                or not _BUILD_ID_PATTERN.fullmatch(self.build_id)
                or not _COMMIT_PATTERN.fullmatch(self.source_commit)
            ):
                raise ValueError("search_packaged_build_identity_invalid")
        elif (
            self.build_id != DEVELOPMENT_BUILD_ID
            or not (
                self.source_commit == "unavailable"
                or _COMMIT_PATTERN.fullmatch(self.source_commit)
            )
        ):
            raise ValueError("search_development_build_identity_invalid")


def load_runtime_build_identity(
    *,
    runtime_root: Path,
    env: Mapping[str, str] | None = None,
) -> BuildIdentity:
    environment = os.environ if env is None else env
    mode = _clean(environment.get("SEARCH_BUILD_MODE") or "development")
    if mode not in {"development", "packaged"}:
        raise ValueError("search_build_mode_invalid")
    configured_path = _clean(environment.get("SEARCH_BUILD_IDENTITY_PATH"))
    package_path = (
        Path(configured_path).expanduser().resolve()
        if configured_path
        else _default_package_path(runtime_root, mode)
    )
    if mode == "packaged":
        package_value = _read_package(package_path)
        raw_identity = package_value.get(BUILD_IDENTITY_PROPERTY)
        if not isinstance(raw_identity, Mapping):
            raise ValueError("search_packaged_build_identity_missing")
        return BuildIdentity.from_dict(
            raw_identity,
            expected_version=_package_version(package_value),
            expected_mode="packaged",
        )
    return _development_identity()


def _development_identity() -> BuildIdentity:
    package_value = _read_package(
        RUNTIME_PROJECT_ROOT / "integrations" / "search_desktop" / "package.json"
    )
    commit, branch = _read_git_identity(RUNTIME_PROJECT_ROOT)
    return BuildIdentity(
        schema_version=BUILD_IDENTITY_SCHEMA_VERSION,
        build_mode="development",
        product="Search",
        version=_package_version(package_value),
        build_id=DEVELOPMENT_BUILD_ID,
        source_commit=commit,
        source_branch=branch,
        build_timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _default_package_path(runtime_root: Path, mode: str) -> Path:
    if mode == "packaged":
        return runtime_root.resolve().parent / "package.json"
    return RUNTIME_PROJECT_ROOT / "integrations" / "search_desktop" / "package.json"


def _read_package(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("search_package_metadata_invalid") from exc
    if not isinstance(value, dict) or value.get("productName") != "Search":
        raise ValueError("search_package_metadata_invalid")
    _package_version(value)
    return value


def _package_version(value: Mapping[str, Any]) -> str:
    version = _clean(value.get("version"))
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("search_package_metadata_invalid")
    return version


def _read_git_identity(root: Path) -> tuple[str, str]:
    commit = _run_git(root, "rev-parse", "HEAD")
    if not _COMMIT_PATTERN.fullmatch(commit):
        return "unavailable", "unavailable"
    branch = _run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
    return commit, branch or "(detached)"


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=5,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _valid_utc_timestamp(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _clean(value: Any) -> str:
    return str(value or "").strip()

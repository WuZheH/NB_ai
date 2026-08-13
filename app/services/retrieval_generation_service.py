from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, LANCEDB_DIR, VECTOR_STORE_DIR
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
)


ACTIVE_POINTER_SCHEMA_VERSION = 1
GENERATION_MANIFEST_SCHEMA_VERSION = 1
ACTIVE_POINTER_NAME = "active_index.json"
ACTIVATION_STATE_NAME = "retrieval_generation_activation.json"
GENERATION_ROOT_NAME = "index_versions"
GENERATION_MANIFEST_NAME = "generation_manifest.json"
FTS_INDEX_NAME = "retrieval_fts_v1.db"
FTS_MANIFEST_NAME = "retrieval_fts_v1_manifest.json"
VECTOR_STORE_NAME = "lancedb"
VECTOR_MANIFEST_NAME = "vector_manifest.json"
NATIVE_NOTE_VECTOR_NAME = "zotero_user_notes_v1"
ACTIVATION_STATE_SCHEMA_VERSION = 1
ACTIVATION_STATUSES = frozenset({"activating", "degraded"})

_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FILE_SHA_CACHE_LOCK = threading.Lock()
_FILE_SHA_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


class RetrievalGenerationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        safe_to_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.safe_to_retry = bool(safe_to_retry)
        self.details = {"safe_to_retry": self.safe_to_retry}


class ActivePointerPublishError(RuntimeError):
    def __init__(self, substage: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.publish_substage = substage
        self.cause = cause


class ActivationStatePublishError(RuntimeError):
    def __init__(self, substage: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.publish_substage = substage
        self.cause = cause


@dataclass(frozen=True)
class RetrievalGenerationSnapshot:
    mode: str
    generation_id: str | None
    production_db_sha256: str
    fts_index_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path
    native_note_vector_path: Path
    generation_dir: Path | None = None


@dataclass(frozen=True)
class CandidateGeneration:
    generation_id: str
    candidate_dir: Path
    final_dir: Path
    fts_index_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path
    native_note_vector_path: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_sha256_file(path: Path) -> str:
    target = Path(path).resolve(strict=True)
    stat = target.stat()
    signature = (int(stat.st_mtime_ns), int(stat.st_size))
    key = str(target)
    with _FILE_SHA_CACHE_LOCK:
        cached = _FILE_SHA_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    digest = sha256_file(target)
    with _FILE_SHA_CACHE_LOCK:
        _FILE_SHA_CACHE[key] = (signature, digest)
    return digest


def tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda value: value.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def active_pointer_path(data_dir: str | Path = DATA_DIR) -> Path:
    return Path(data_dir).resolve(strict=False) / ACTIVE_POINTER_NAME


def activation_state_path(data_dir: str | Path = DATA_DIR) -> Path:
    return Path(data_dir).resolve(strict=False) / ACTIVATION_STATE_NAME


def _path_entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _path_is_reparse_point(path: Path) -> bool:
    """Return whether a path entry is a Windows reparse point without following it."""

    try:
        attributes = int(getattr(os.lstat(path), "st_file_attributes", 0) or 0)
    except (OSError, TypeError, ValueError):
        return False
    return bool(attributes & 0x400)


def _invalid_generation_root(message: str) -> RetrievalGenerationError:
    return RetrievalGenerationError(
        "active_index_invalid",
        message,
        safe_to_retry=False,
    )


def _validated_generation_root(
    data_dir: str | Path,
    *,
    create: bool = False,
) -> Path:
    data_root = Path(data_dir).resolve(strict=False)
    root = data_root / GENERATION_ROOT_NAME
    if not _path_entry_exists(root):
        if not create:
            return root
        root.mkdir(parents=True, exist_ok=False)
    if (
        root.is_symlink()
        or _path_is_reparse_point(root)
        or not root.is_dir()
    ):
        raise _invalid_generation_root(
            "Retrieval generation root must be an ordinary controlled directory."
        )
    try:
        if root.parent.resolve(strict=True) != data_root.resolve(strict=True):
            raise _invalid_generation_root(
                "Retrieval generation root escapes the controlled data directory."
            )
    except OSError as exc:
        raise _invalid_generation_root(
            "Retrieval generation root is unreadable."
        ) from exc
    return root


def generation_root(data_dir: str | Path = DATA_DIR) -> Path:
    return _validated_generation_root(data_dir)


def _safe_generation_id(value: Any) -> str:
    generation_id = str(value or "").strip()
    if (
        not generation_id
        or generation_id in {".", ".."}
        or not _GENERATION_ID.fullmatch(generation_id)
    ):
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation id is invalid.",
        )
    return generation_id


def _contained_existing_path(root: Path, path: Path) -> Path:
    if (
        not _path_entry_exists(root)
        or root.is_symlink()
        or _path_is_reparse_point(root)
        or not root.is_dir()
    ):
        raise _invalid_generation_root(
            "Retrieval generation root is missing or unsafe."
        )
    try:
        root_resolved = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation path is missing or unreadable.",
        ) from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation escapes the controlled root.",
        ) from exc
    current = path
    while current != root and current != current.parent:
        if current.is_symlink() or _path_is_reparse_point(current):
            raise RetrievalGenerationError(
                "active_index_invalid",
                "Active retrieval generation contains a link or reparse point.",
            )
        current = current.parent
    return resolved


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation metadata is unreadable.",
        ) from exc
    if not isinstance(parsed, dict):
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation metadata is invalid.",
        )
    return parsed


def _legacy_snapshot(*, data_dir: Path, db_path: Path) -> RetrievalGenerationSnapshot:
    return RetrievalGenerationSnapshot(
        mode="legacy",
        generation_id=None,
        production_db_sha256=cached_sha256_file(db_path),
        fts_index_path=(data_dir / "search_index" / FTS_INDEX_NAME),
        fts_manifest_path=(data_dir / "search_index" / FTS_MANIFEST_NAME),
        vector_store_path=(data_dir / "vector_store" / VECTOR_STORE_NAME),
        vector_manifest_path=(data_dir / "vector_store" / VECTOR_MANIFEST_NAME),
        native_note_vector_path=(data_dir / "vector_store" / NATIVE_NOTE_VECTOR_NAME),
    )


def _fail_invalid_legacy_state(message: str) -> RetrievalGenerationError:
    return RetrievalGenerationError(
        "active_index_invalid",
        message,
        safe_to_retry=False,
    )


def _resolve_proven_legacy_snapshot(
    *,
    data_dir: Path,
    db_path: Path,
) -> RetrievalGenerationSnapshot:
    """Return legacy paths only after proving the legacy revision is coherent.

    Pointer absence alone is ambiguous once generation publishing is supported:
    it may mean a clean pre-generation installation, an interrupted first
    activation, or a deleted versioned pointer.  Generation residue and the
    legacy FTS manifest therefore form part of the proof, rather than being
    treated as optional status metadata.
    """

    root = data_dir / GENERATION_ROOT_NAME
    if _path_entry_exists(root):
        if (
            root.is_symlink()
            or _path_is_reparse_point(root)
            or not root.is_dir()
        ):
            raise _fail_invalid_legacy_state(
                "Retrieval generation root is invalid while the active pointer is absent."
            )
        try:
            has_generation_entries = next(root.iterdir(), None) is not None
        except OSError as exc:
            raise _fail_invalid_legacy_state(
                "Retrieval generation root is unreadable while the active pointer is absent."
            ) from exc
        if has_generation_entries:
            raise _fail_invalid_legacy_state(
                "Retrieval generation state exists while the active pointer is absent."
            )

    manifest_path = data_dir / "search_index" / FTS_MANIFEST_NAME
    if (
        not _path_entry_exists(manifest_path)
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise _fail_invalid_legacy_state(
            "Legacy retrieval manifest is missing or invalid."
        )
    manifest = _read_json_object(manifest_path)
    manifest_db_sha = str(manifest.get("production_db_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_db_sha):
        raise _fail_invalid_legacy_state(
            "Legacy retrieval database revision is invalid."
        )
    try:
        database_sha = cached_sha256_file(db_path).lower()
    except (OSError, RuntimeError) as exc:
        raise _fail_invalid_legacy_state(
            "Production database revision cannot be verified for legacy retrieval."
        ) from exc
    if database_sha != manifest_db_sha:
        raise RetrievalGenerationError(
            "active_index_database_revision_mismatch",
            "Legacy retrieval generation does not match the production database.",
            safe_to_retry=False,
        )
    return _legacy_snapshot(data_dir=data_dir, db_path=db_path)


def resolve_active_retrieval_generation(
    *,
    data_dir: str | Path = DATA_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
    verify_fingerprints: bool = True,
) -> RetrievalGenerationSnapshot:
    data_root = Path(data_dir).resolve(strict=False)
    database = Path(db_path).resolve(strict=False)
    pointer_path = data_root / ACTIVE_POINTER_NAME
    if not _path_entry_exists(pointer_path):
        return _resolve_proven_legacy_snapshot(
            data_dir=data_root,
            db_path=database,
        )
    if not pointer_path.is_file() or pointer_path.is_symlink():
        raise RetrievalGenerationError(
            "active_index_invalid", "Active retrieval generation pointer is invalid."
        )

    pointer = _read_json_object(pointer_path)
    if pointer.get("schema_version") != ACTIVE_POINTER_SCHEMA_VERSION:
        raise RetrievalGenerationError(
            "active_index_invalid", "Active retrieval generation schema is unsupported."
        )
    generation_id = _safe_generation_id(pointer.get("generation_id"))
    pointer_db_sha = str(pointer.get("production_db_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pointer_db_sha):
        raise RetrievalGenerationError(
            "active_index_invalid", "Active retrieval database revision is invalid."
        )

    root = _validated_generation_root(data_root)
    if not _path_entry_exists(root):
        raise _invalid_generation_root(
            "Active retrieval generation root is missing."
        )
    generation_dir = _contained_existing_path(root, root / generation_id)
    manifest_path = _contained_existing_path(
        root, generation_dir / GENERATION_MANIFEST_NAME
    )
    manifest = _read_json_object(manifest_path)
    if manifest.get("schema_version") != GENERATION_MANIFEST_SCHEMA_VERSION:
        raise RetrievalGenerationError(
            "active_index_invalid", "Retrieval generation manifest schema is unsupported."
        )
    if str(manifest.get("generation_id") or "") != generation_id:
        raise RetrievalGenerationError(
            "active_index_invalid", "Retrieval generation identity does not match."
        )
    manifest_db_sha = str(manifest.get("production_db_sha256") or "").lower()
    if manifest_db_sha != pointer_db_sha:
        raise RetrievalGenerationError(
            "active_index_invalid", "Retrieval generation database revision does not match."
        )

    paths = {
        "fts_index_path": generation_dir / FTS_INDEX_NAME,
        "fts_manifest_path": generation_dir / FTS_MANIFEST_NAME,
        "vector_store_path": generation_dir / VECTOR_STORE_NAME,
        "vector_manifest_path": generation_dir / VECTOR_MANIFEST_NAME,
        "native_note_vector_path": generation_dir / NATIVE_NOTE_VECTOR_NAME,
    }
    resolved = {name: _contained_existing_path(root, path) for name, path in paths.items()}
    if not resolved["fts_index_path"].is_file() or not resolved["fts_manifest_path"].is_file():
        raise RetrievalGenerationError("active_index_invalid", "Retrieval generation FTS is incomplete.")
    if not resolved["vector_store_path"].is_dir() or not resolved["vector_manifest_path"].is_file():
        raise RetrievalGenerationError("active_index_invalid", "Retrieval generation vector store is incomplete.")
    if not resolved["native_note_vector_path"].is_dir():
        raise RetrievalGenerationError("active_index_invalid", "Retrieval generation note vectors are incomplete.")

    if verify_fingerprints:
        expected = {
            "fts_index_sha256": sha256_file(resolved["fts_index_path"]),
            "fts_manifest_sha256": sha256_file(resolved["fts_manifest_path"]),
            "vector_store_tree_fingerprint": tree_fingerprint(resolved["vector_store_path"]),
            "vector_manifest_sha256": sha256_file(resolved["vector_manifest_path"]),
            "native_note_vector_tree_fingerprint": tree_fingerprint(resolved["native_note_vector_path"]),
        }
        for field, actual in expected.items():
            if str(manifest.get(field) or "").lower() != actual.lower():
                raise RetrievalGenerationError(
                    "active_index_invalid",
                    f"Retrieval generation fingerprint mismatch: {field}.",
                )

    return RetrievalGenerationSnapshot(
        mode="versioned",
        generation_id=generation_id,
        production_db_sha256=pointer_db_sha,
        generation_dir=generation_dir,
        **resolved,
    )


def new_generation_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"g-{timestamp}-{uuid4().hex[:6]}"


def prepare_candidate_generation(
    source: RetrievalGenerationSnapshot,
    *,
    data_dir: str | Path = DATA_DIR,
    generation_id: str | None = None,
) -> CandidateGeneration:
    selected_id = _safe_generation_id(generation_id or new_generation_id())
    root = _validated_generation_root(data_dir, create=True)
    final_dir = root / selected_id
    # Keep the transient directory short for Windows MAX_PATH compatibility.
    candidate_dir = root / f".c-{uuid4().hex[:8]}"
    if _path_entry_exists(final_dir) or _path_entry_exists(candidate_dir):
        raise FileExistsError(selected_id)
    candidate_dir.mkdir(parents=False, exist_ok=False)
    candidate = CandidateGeneration(
        generation_id=selected_id,
        candidate_dir=candidate_dir,
        final_dir=final_dir,
        fts_index_path=candidate_dir / FTS_INDEX_NAME,
        fts_manifest_path=candidate_dir / FTS_MANIFEST_NAME,
        vector_store_path=candidate_dir / VECTOR_STORE_NAME,
        vector_manifest_path=candidate_dir / VECTOR_MANIFEST_NAME,
        native_note_vector_path=candidate_dir / NATIVE_NOTE_VECTOR_NAME,
    )
    try:
        shutil.copy2(source.fts_index_path, candidate.fts_index_path)
        shutil.copy2(source.fts_manifest_path, candidate.fts_manifest_path)
        shutil.copytree(source.vector_store_path, candidate.vector_store_path)
        shutil.copy2(source.vector_manifest_path, candidate.vector_manifest_path)
        shutil.copytree(source.native_note_vector_path, candidate.native_note_vector_path)
    except BaseException:
        shutil.rmtree(candidate_dir, ignore_errors=True)
        raise
    return candidate


def finalize_candidate_generation(
    candidate: CandidateGeneration,
    *,
    production_db_sha256: str,
    profile_versions: dict[str, Any] | None = None,
) -> RetrievalGenerationSnapshot:
    root = candidate.candidate_dir.parent
    expected_root = _validated_generation_root(root.parent)
    if root != expected_root:
        raise _invalid_generation_root(
            "Candidate generation is outside the controlled generation root."
        )
    if (
        candidate.final_dir.parent != root
        or candidate.candidate_dir.parent != root
        or candidate.final_dir.name != candidate.generation_id
        or candidate.candidate_dir.is_symlink()
        or _path_is_reparse_point(candidate.candidate_dir)
        or not candidate.candidate_dir.is_dir()
        or _path_entry_exists(candidate.final_dir)
    ):
        raise _invalid_generation_root(
            "Candidate generation paths are invalid or unsafe."
        )
    _contained_existing_path(root, candidate.candidate_dir)
    db_sha = str(production_db_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", db_sha):
        raise ValueError("production_db_sha256 must be a SHA256 digest")
    manifest = {
        "schema_version": GENERATION_MANIFEST_SCHEMA_VERSION,
        "generation_id": candidate.generation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "production_db_sha256": db_sha,
        "fts_index_sha256": sha256_file(candidate.fts_index_path),
        "fts_manifest_sha256": sha256_file(candidate.fts_manifest_path),
        "vector_store_tree_fingerprint": tree_fingerprint(candidate.vector_store_path),
        "vector_manifest_sha256": sha256_file(candidate.vector_manifest_path),
        "native_note_vector_tree_fingerprint": tree_fingerprint(candidate.native_note_vector_path),
        "profile_versions": dict(profile_versions or {}),
    }
    manifest_path = candidate.candidate_dir / GENERATION_MANIFEST_NAME
    _write_json_fsync(manifest_path, manifest)
    os.replace(candidate.candidate_dir, candidate.final_dir)
    return RetrievalGenerationSnapshot(
        mode="versioned",
        generation_id=candidate.generation_id,
        production_db_sha256=db_sha,
        generation_dir=candidate.final_dir,
        fts_index_path=candidate.final_dir / FTS_INDEX_NAME,
        fts_manifest_path=candidate.final_dir / FTS_MANIFEST_NAME,
        vector_store_path=candidate.final_dir / VECTOR_STORE_NAME,
        vector_manifest_path=candidate.final_dir / VECTOR_MANIFEST_NAME,
        native_note_vector_path=candidate.final_dir / NATIVE_NOTE_VECTOR_NAME,
    )


def _write_json_fsync(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_json_fsync(
    path: Path,
    payload: dict[str, Any],
    *,
    write_substage: str,
    replace_substage: str,
) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        try:
            _write_json_fsync(temporary, payload)
        except BaseException as exc:
            raise ActivationStatePublishError(write_substage, exc) from exc
        try:
            os.replace(temporary, path)
        except BaseException as exc:
            raise ActivationStatePublishError(replace_substage, exc) from exc
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _activation_error(message: str) -> RetrievalGenerationError:
    return RetrievalGenerationError(
        "retrieval_generation_degraded",
        message,
        safe_to_retry=False,
    )


def _read_activation_state(
    *,
    data_dir: str | Path = DATA_DIR,
) -> dict[str, Any] | None:
    data_root = Path(data_dir).resolve(strict=False)
    state_path = activation_state_path(data_root)
    if not _path_entry_exists(state_path):
        return None
    if not state_path.is_file() or state_path.is_symlink():
        raise _activation_error(
            "Retrieval generation activation state is invalid; read access is blocked."
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _activation_error(
            "Retrieval generation activation state is unreadable; read access is blocked."
        ) from exc
    if not isinstance(state, dict):
        raise _activation_error(
            "Retrieval generation activation state is invalid; read access is blocked."
        )
    allowed_fields = {
        "schema_version",
        "status",
        "previous_generation_id",
        "candidate_generation_id",
        "production_db_sha256",
        "created_at",
        "error_code",
        "publish_substage",
    }
    if set(state) - allowed_fields:
        raise _activation_error(
            "Retrieval generation activation state contains unsupported fields."
        )
    if state.get("schema_version") != ACTIVATION_STATE_SCHEMA_VERSION:
        raise _activation_error(
            "Retrieval generation activation state schema is unsupported."
        )
    status = str(state.get("status") or "").strip()
    if status not in ACTIVATION_STATUSES:
        raise _activation_error(
            "Retrieval generation activation status is invalid."
        )
    candidate_id = _safe_activation_generation_id(
        state.get("candidate_generation_id"), required=True
    )
    previous_id = _safe_activation_generation_id(
        state.get("previous_generation_id"), required=False
    )
    db_sha = str(state.get("production_db_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", db_sha):
        raise _activation_error(
            "Retrieval generation activation database revision is invalid."
        )
    created_at = str(state.get("created_at") or "").strip()
    if not created_at:
        raise _activation_error(
            "Retrieval generation activation timestamp is invalid."
        )

    try:
        root = generation_root(data_root)
        _contained_existing_path(root, root / candidate_id)
        if previous_id is not None:
            _contained_existing_path(root, root / previous_id)
    except RetrievalGenerationError as exc:
        raise _activation_error(
            "Retrieval generation activation references an invalid generation."
        ) from exc

    pointer = active_pointer_path(data_root)
    active_id: str | None = None
    active_sha: str | None = None
    if _path_entry_exists(pointer):
        if not pointer.is_file() or pointer.is_symlink():
            raise _activation_error(
                "Retrieval generation activation pointer is invalid."
            )
        try:
            pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise _activation_error(
                "Retrieval generation activation pointer is unreadable."
            ) from exc
        if (
            not isinstance(pointer_payload, dict)
            or pointer_payload.get("schema_version")
            != ACTIVE_POINTER_SCHEMA_VERSION
        ):
            raise _activation_error(
                "Retrieval generation activation pointer is invalid."
            )
        active_id = _safe_activation_generation_id(
            pointer_payload.get("generation_id"), required=True
        )
        active_sha = str(
            pointer_payload.get("production_db_sha256") or ""
        ).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", active_sha):
            raise _activation_error(
                "Retrieval generation activation pointer revision is invalid."
            )

    allowed_active_ids = {candidate_id, previous_id}
    if active_id not in allowed_active_ids:
        raise _activation_error(
            "Retrieval generation activation and active pointer are inconsistent."
        )
    if status == "degraded" and active_id != candidate_id:
        raise _activation_error(
            "Degraded retrieval generation does not match the active pointer."
        )
    if active_id == candidate_id and active_sha != db_sha:
        raise _activation_error(
            "Retrieval generation activation database revision is inconsistent."
        )

    return {
        "schema_version": ACTIVATION_STATE_SCHEMA_VERSION,
        "status": status,
        "previous_generation_id": previous_id,
        "candidate_generation_id": candidate_id,
        "production_db_sha256": db_sha,
        "created_at": created_at,
        "error_code": _safe_optional_activation_text(state.get("error_code")),
        "publish_substage": _safe_optional_activation_text(
            state.get("publish_substage")
        ),
    }


def _safe_activation_generation_id(value: Any, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    try:
        return _safe_generation_id(value)
    except RetrievalGenerationError as exc:
        raise _activation_error(
            "Retrieval generation activation identity is invalid."
        ) from exc


def _safe_optional_activation_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _activation_error(
            "Retrieval generation activation metadata is invalid."
        )
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 128 or not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
        raise _activation_error(
            "Retrieval generation activation metadata is invalid."
        )
    return normalized


def begin_generation_activation(
    previous: RetrievalGenerationSnapshot,
    candidate: RetrievalGenerationSnapshot,
    *,
    production_db_sha256: str,
    data_dir: str | Path = DATA_DIR,
) -> dict[str, Any]:
    root = _validated_generation_root(data_dir)
    if not _path_entry_exists(root):
        raise _invalid_generation_root(
            "Retrieval generation root is missing during activation."
        )
    if candidate.generation_dir is None:
        raise _invalid_generation_root(
            "Candidate generation directory is missing during activation."
        )
    _contained_existing_path(root, candidate.generation_dir)
    if previous.mode == "versioned":
        if previous.generation_dir is None:
            raise _invalid_generation_root(
                "Previous generation directory is missing during activation."
            )
        _contained_existing_path(root, previous.generation_dir)
    if candidate.mode != "versioned" or not candidate.generation_id:
        raise ValueError("candidate must be a versioned generation")
    db_sha = str(production_db_sha256 or "").lower()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", db_sha)
        or db_sha != candidate.production_db_sha256.lower()
    ):
        raise ValueError("candidate production database SHA256 does not match")
    payload = {
        "schema_version": ACTIVATION_STATE_SCHEMA_VERSION,
        "status": "activating",
        "previous_generation_id": previous.generation_id,
        "candidate_generation_id": candidate.generation_id,
        "production_db_sha256": db_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path = activation_state_path(data_dir)
    if _path_entry_exists(state_path):
        raise ActivationStatePublishError(
            "activation_state_preexisting",
            FileExistsError(state_path.name),
        )
    _replace_json_fsync(
        state_path,
        payload,
        write_substage="activation_state_write",
        replace_substage="activation_state_replace",
    )
    return payload


def mark_activation_degraded(
    *,
    data_dir: str | Path = DATA_DIR,
    error_code: str = "active_pointer_rollback_failed",
    publish_substage: str = "active_pointer_rollback",
) -> dict[str, Any]:
    state = _read_activation_state(data_dir=data_dir)
    if state is None:
        raise ActivationStatePublishError(
            "activation_state_degraded_missing",
            FileNotFoundError(ACTIVATION_STATE_NAME),
        )
    payload = {
        **state,
        "status": "degraded",
        "error_code": _safe_optional_activation_text(error_code),
        "publish_substage": _safe_optional_activation_text(publish_substage),
    }
    _replace_json_fsync(
        activation_state_path(data_dir),
        payload,
        write_substage="activation_state_degraded_write",
        replace_substage="activation_state_degraded_replace",
    )
    return payload


def clear_activation_state(*, data_dir: str | Path = DATA_DIR) -> None:
    state_path = activation_state_path(data_dir)
    if not _path_entry_exists(state_path):
        return
    root = _validated_generation_root(data_dir)
    if not _path_entry_exists(root):
        raise ActivationStatePublishError(
            "activation_state_clear",
            OSError("generation root is missing"),
        )
    if not state_path.is_file() or state_path.is_symlink():
        raise ActivationStatePublishError(
            "activation_state_clear",
            OSError("activation state path is invalid"),
        )
    try:
        state_path.unlink()
        _fsync_directory(state_path.parent)
    except BaseException as exc:
        raise ActivationStatePublishError("activation_state_clear", exc) from exc


def assert_activation_allows_read(
    *, data_dir: str | Path = DATA_DIR
) -> None:
    state_path = activation_state_path(data_dir)
    if not _path_entry_exists(state_path):
        return
    # Parse for deterministic validation, but any surviving marker is a durable
    # fail-closed latch, including an interrupted "activating" transition.
    _read_activation_state(data_dir=data_dir)
    raise _activation_error(
        "Retrieval generation activation is incomplete; read access is blocked."
    )


def _pointer_payload(snapshot: RetrievalGenerationSnapshot) -> dict[str, Any]:
    if snapshot.mode != "versioned" or not snapshot.generation_id:
        raise ValueError("only a versioned generation can be activated")
    return {
        "schema_version": ACTIVE_POINTER_SCHEMA_VERSION,
        "generation_id": snapshot.generation_id,
        "production_db_sha256": snapshot.production_db_sha256,
    }


def publish_active_generation(
    snapshot: RetrievalGenerationSnapshot,
    *,
    data_dir: str | Path = DATA_DIR,
) -> None:
    root = _validated_generation_root(data_dir)
    if not _path_entry_exists(root) or snapshot.generation_dir is None:
        raise _invalid_generation_root(
            "Published retrieval generation is outside a valid controlled root."
        )
    resolved_generation = _contained_existing_path(root, snapshot.generation_dir)
    if (
        snapshot.generation_id is None
        or resolved_generation != (root / snapshot.generation_id).resolve(strict=True)
    ):
        raise _invalid_generation_root(
            "Published retrieval generation identity is invalid."
        )
    pointer = active_pointer_path(data_dir)
    if _path_entry_exists(pointer) and (
        pointer.is_symlink()
        or _path_is_reparse_point(pointer)
        or not pointer.is_file()
    ):
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation pointer is invalid.",
            safe_to_retry=False,
        )
    temporary = pointer.with_name(f"{pointer.name}.{uuid4().hex}.tmp")
    try:
        try:
            _write_json_fsync(temporary, _pointer_payload(snapshot))
        except BaseException as exc:
            raise ActivePointerPublishError("active_pointer_write", exc) from exc
        try:
            os.replace(temporary, pointer)
        except BaseException as exc:
            raise ActivePointerPublishError("active_pointer_replace", exc) from exc
        _fsync_directory(pointer.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    invalidate_generation_validation_cache()


def restore_active_pointer(
    previous_bytes: bytes | None,
    *,
    data_dir: str | Path = DATA_DIR,
) -> None:
    root = _validated_generation_root(data_dir)
    if not _path_entry_exists(root):
        raise _invalid_generation_root(
            "Retrieval generation root is missing during pointer restore."
        )
    pointer = active_pointer_path(data_dir)
    if _path_entry_exists(pointer) and (
        pointer.is_symlink()
        or _path_is_reparse_point(pointer)
        or not pointer.is_file()
    ):
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation pointer is invalid.",
            safe_to_retry=False,
        )
    temporary = pointer.with_name(f"{pointer.name}.{uuid4().hex}.rollback.tmp")
    try:
        if previous_bytes is None:
            pointer.unlink(missing_ok=True)
            _fsync_directory(pointer.parent)
        else:
            with temporary.open("xb") as handle:
                handle.write(previous_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, pointer)
            _fsync_directory(pointer.parent)
    except BaseException as exc:
        raise ActivePointerPublishError("active_pointer_rollback", exc) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    invalidate_generation_validation_cache()


def read_active_pointer_bytes(*, data_dir: str | Path = DATA_DIR) -> bytes | None:
    pointer = active_pointer_path(data_dir)
    if _path_entry_exists(pointer) and (
        pointer.is_symlink()
        or _path_is_reparse_point(pointer)
        or not pointer.is_file()
    ):
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation pointer is invalid during restore.",
            safe_to_retry=False,
        )
    if not _path_entry_exists(pointer):
        return None
    if pointer.is_symlink() or _path_is_reparse_point(pointer) or not pointer.is_file():
        raise RetrievalGenerationError(
            "active_index_invalid",
            "Active retrieval generation pointer is invalid.",
            safe_to_retry=False,
        )
    return pointer.read_bytes()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ProductionGenerationCoordinator:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._readers = 0
        self._writer_thread: int | None = None
        self._writer_depth = 0
        self._waiting_writers = 0
        self._local = threading.local()
        self._degraded_reason: str | None = None

    def _degraded_error(self) -> RetrievalGenerationError:
        return RetrievalGenerationError(
            "retrieval_generation_degraded",
            "Retrieval generation is degraded and read access is blocked.",
            safe_to_retry=False,
        )

    def assert_readable(self) -> None:
        with self._condition:
            if self._degraded_reason is not None:
                raise self._degraded_error()

    def mark_degraded(self, reason: str) -> None:
        normalized = str(reason or "").strip()
        if not normalized:
            raise ValueError("degraded reason is required")
        with self._condition:
            if self._writer_thread != threading.get_ident():
                raise RuntimeError("degraded state requires the production writer lock")
            self._degraded_reason = normalized
            self._condition.notify_all()

    def clear_degraded(self) -> None:
        with self._condition:
            if self._writer_thread != threading.get_ident():
                raise RuntimeError("degraded recovery requires the production writer lock")
            self._degraded_reason = None
            self._condition.notify_all()

    @property
    def degraded(self) -> bool:
        with self._condition:
            return self._degraded_reason is not None

    @property
    def degraded_reason(self) -> str | None:
        with self._condition:
            return self._degraded_reason

    @contextmanager
    def read(self) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            if self._degraded_reason is not None:
                raise self._degraded_error()
            if self._writer_thread == thread_id:
                yield_from_writer = True
            else:
                yield_from_writer = False
                depth = int(getattr(self._local, "read_depth", 0))
                if depth == 0:
                    while self._writer_thread is not None or self._waiting_writers:
                        self._condition.wait()
                        if self._degraded_reason is not None:
                            raise self._degraded_error()
                    if self._degraded_reason is not None:
                        raise self._degraded_error()
                    self._readers += 1
                self._local.read_depth = depth + 1
        try:
            yield
        finally:
            if not yield_from_writer:
                with self._condition:
                    depth = int(getattr(self._local, "read_depth", 1)) - 1
                    self._local.read_depth = depth
                    if depth == 0:
                        self._readers -= 1
                        self._condition.notify_all()

    @contextmanager
    def write(self, *, allow_degraded: bool = False) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            if self._writer_thread == thread_id:
                if self._degraded_reason is not None and not allow_degraded:
                    raise self._degraded_error()
                self._writer_depth += 1
            else:
                if int(getattr(self._local, "read_depth", 0)):
                    raise RuntimeError("read-to-write lock upgrade is not supported")
                self._waiting_writers += 1
                try:
                    while self._writer_thread is not None or self._readers:
                        self._condition.wait()
                    if self._degraded_reason is not None and not allow_degraded:
                        raise self._degraded_error()
                    self._writer_thread = thread_id
                    self._writer_depth = 1
                finally:
                    self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_depth -= 1
                if self._writer_depth == 0:
                    self._writer_thread = None
                    self._condition.notify_all()


PRODUCTION_GENERATION_COORDINATOR = ProductionGenerationCoordinator()
_PINNED_GENERATION: ContextVar[RetrievalGenerationSnapshot | None] = ContextVar(
    "pinned_retrieval_generation", default=None
)
_VALIDATION_CACHE_LOCK = threading.Lock()
_VALIDATION_CACHE: dict[tuple[str, str], tuple[int, int]] = {}


def invalidate_generation_validation_cache() -> None:
    with _VALIDATION_CACHE_LOCK:
        _VALIDATION_CACHE.clear()


def _validate_database_revision(snapshot: RetrievalGenerationSnapshot, db_path: Path) -> None:
    if snapshot.mode != "versioned":
        return
    stat = db_path.stat()
    signature = (int(stat.st_mtime_ns), int(stat.st_size))
    key = (str(db_path.resolve(strict=False)), snapshot.production_db_sha256)
    with _VALIDATION_CACHE_LOCK:
        if _VALIDATION_CACHE.get(key) == signature:
            return
    if sha256_file(db_path).lower() != snapshot.production_db_sha256.lower():
        raise RetrievalGenerationError(
            "active_index_database_revision_mismatch",
            "Active retrieval generation does not match the production database.",
        )
    with _VALIDATION_CACHE_LOCK:
        _VALIDATION_CACHE[key] = signature


def verify_generation_database_revision(
    snapshot: RetrievalGenerationSnapshot,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    _validate_database_revision(snapshot, Path(db_path).resolve(strict=False))


@contextmanager
def production_read_generation(
    *,
    data_dir: str | Path = DATA_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Iterator[RetrievalGenerationSnapshot]:
    assert_activation_allows_read(data_dir=data_dir)
    PRODUCTION_GENERATION_COORDINATOR.assert_readable()
    existing = _PINNED_GENERATION.get()
    if existing is not None:
        yield existing
        return
    with PRODUCTION_GENERATION_COORDINATOR.read():
        snapshot = resolve_active_retrieval_generation(data_dir=data_dir, db_path=db_path)
        _validate_database_revision(snapshot, Path(db_path).resolve(strict=False))
        token = _PINNED_GENERATION.set(snapshot)
        try:
            yield snapshot
        finally:
            _PINNED_GENERATION.reset(token)


@contextmanager
def production_write_generation(
    *, data_dir: str | Path = DATA_DIR
) -> Iterator[None]:
    assert_activation_allows_read(data_dir=data_dir)
    with PRODUCTION_GENERATION_COORDINATOR.write():
        token = _PINNED_GENERATION.set(None)
        try:
            yield
        finally:
            _PINNED_GENERATION.reset(token)


def current_retrieval_generation(
    *,
    data_dir: str | Path = DATA_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> RetrievalGenerationSnapshot:
    assert_activation_allows_read(data_dir=data_dir)
    PRODUCTION_GENERATION_COORDINATOR.assert_readable()
    pinned = _PINNED_GENERATION.get()
    if pinned is not None:
        return pinned
    snapshot = resolve_active_retrieval_generation(data_dir=data_dir, db_path=db_path)
    _validate_database_revision(snapshot, Path(db_path).resolve(strict=False))
    return snapshot


def revalidate_active_generation(
    *,
    data_dir: str | Path = DATA_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
    validator: Callable[[RetrievalGenerationSnapshot], None] | None = None,
) -> RetrievalGenerationSnapshot:
    """Fully validate durable activation state before restoring read access."""

    with PRODUCTION_GENERATION_COORDINATOR.write(allow_degraded=True):
        invalidate_generation_validation_cache()
        activation = _read_activation_state(data_dir=data_dir)
        try:
            snapshot = resolve_active_retrieval_generation(
                data_dir=data_dir,
                db_path=db_path,
                verify_fingerprints=True,
            )
        except RetrievalGenerationError as exc:
            raise RetrievalGenerationError(
                "retrieval_generation_revalidation_failed",
                "Active retrieval generation could not be revalidated.",
                safe_to_retry=False,
            ) from exc
        if activation is None and snapshot.mode != "versioned":
            raise RetrievalGenerationError(
                "retrieval_generation_revalidation_failed",
                "A degraded retrieval generation cannot recover through legacy fallback.",
                safe_to_retry=False,
            )
        if activation is not None:
            previous_id = activation["previous_generation_id"]
            candidate_id = activation["candidate_generation_id"]
            active_id = snapshot.generation_id
            if active_id not in {previous_id, candidate_id}:
                raise RetrievalGenerationError(
                    "retrieval_generation_revalidation_failed",
                    "Active retrieval generation does not match durable activation state.",
                    safe_to_retry=False,
                )
            if active_id == candidate_id and (
                snapshot.mode != "versioned"
                or snapshot.production_db_sha256
                != activation["production_db_sha256"]
            ):
                raise RetrievalGenerationError(
                    "retrieval_generation_revalidation_failed",
                    "Candidate retrieval generation revision does not match durable activation state.",
                    safe_to_retry=False,
                )
            if active_id == previous_id and previous_id is None and snapshot.mode != "legacy":
                raise RetrievalGenerationError(
                    "retrieval_generation_revalidation_failed",
                    "Legacy rollback state does not match durable activation state.",
                    safe_to_retry=False,
                )
        _validate_database_revision(
            snapshot,
            Path(db_path).resolve(strict=False),
        )
        if validator is None:
            raise RetrievalGenerationError(
                "retrieval_generation_revalidation_failed",
                "Explicit full generation validation is required before recovery.",
                safe_to_retry=False,
            )
        validator(snapshot)
        if activation is not None:
            clear_activation_state(data_dir=data_dir)
        PRODUCTION_GENERATION_COORDINATOR.clear_degraded()
        return snapshot


def product_read_generation_guard(function):
    """Pin one validated generation for an entire product read request."""

    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            with production_read_generation():
                return function(*args, **kwargs)
        except RetrievalGenerationError as exc:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail={
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                    "retryable": False,
                    "safe_to_retry": False,
                    "writes_performed": False,
                },
            ) from exc

    return guarded

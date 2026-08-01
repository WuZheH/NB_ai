from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_HISTORY_FIELDS = (
    "previewed_at",
    "confirmed_at",
    "transaction_fingerprint",
    "confirmation_token_fingerprint",
    "source_revision_fingerprint",
)


class LocalPdfSourceBindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalPdfSourceBinding:
    source_identity: str
    pdf_sha256: str
    source_revision_fingerprint: str
    managed_pdf_relative_path: str
    import_history: Mapping[str, Any]

    def source_trace(self) -> dict[str, Any]:
        validate_binding(self)
        return {
            "schema_version": "local_pdf_source_trace.v1",
            "source_type": "local_pdf",
            "source_identity": self.source_identity,
            "pdf_sha256": self.pdf_sha256,
            "source_pdf_sha256": self.pdf_sha256,
            "source_revision_fingerprint": (
                self.source_revision_fingerprint
            ),
            "managed_pdf_path": self.managed_pdf_relative_path,
            "import_history": dict(self.import_history),
        }


def validate_binding(binding: LocalPdfSourceBinding) -> None:
    if not binding.source_identity.startswith("local_pdf:sha256:"):
        raise LocalPdfSourceBindingError(
            "local_pdf_source_identity_invalid"
        )
    _require_sha(binding.pdf_sha256, "local_pdf_pdf_sha256_invalid")
    _require_sha(
        binding.source_revision_fingerprint,
        "local_pdf_source_revision_invalid",
    )
    expected_identity = f"local_pdf:sha256:{binding.pdf_sha256}"
    if binding.source_identity != expected_identity:
        raise LocalPdfSourceBindingError(
            "local_pdf_source_identity_mismatch"
        )
    _validate_safe_relative_path(binding.managed_pdf_relative_path)
    history = dict(binding.import_history)
    for field in _REQUIRED_HISTORY_FIELDS:
        value = str(history.get(field) or "").strip()
        if not value:
            raise LocalPdfSourceBindingError(
                f"local_pdf_import_history_{field}_missing"
            )
    for field in (
        "transaction_fingerprint",
        "confirmation_token_fingerprint",
        "source_revision_fingerprint",
    ):
        _require_sha(
            str(history[field]),
            f"local_pdf_import_history_{field}_invalid",
        )
    if history["source_revision_fingerprint"] != (
        binding.source_revision_fingerprint
    ):
        raise LocalPdfSourceBindingError(
            "local_pdf_import_history_revision_mismatch"
        )
    events = history.get("lifecycle_events")
    if not isinstance(events, (list, tuple)) or not events:
        raise LocalPdfSourceBindingError(
            "local_pdf_import_history_lifecycle_events_missing"
        )
    forbidden = {
        "confirmation_token",
        "raw_confirmation_token",
        "source_path",
        "pdf_path",
        "inbox_path",
    }
    if forbidden.intersection(history):
        raise LocalPdfSourceBindingError(
            "local_pdf_import_history_contains_private_fields"
        )


def record_document_source(
    *,
    db_path: Path,
    document_id: int,
    binding: LocalPdfSourceBinding,
) -> dict[str, Any]:
    trace = binding.source_trace()
    serialized = json.dumps(
        trace,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(Path(db_path)) as connection:
        columns = _columns(connection, "document_sources")
        required = {"document_id", "source_type", "source_trace_json"}
        if not required.issubset(columns):
            raise LocalPdfSourceBindingError(
                "local_pdf_document_sources_contract_missing"
            )
        rows = connection.execute(
            """
            SELECT rowid, source_trace_json
            FROM document_sources
            WHERE document_id = ? AND source_type = 'local_pdf'
            ORDER BY rowid
            """,
            (int(document_id),),
        ).fetchall()
        if len(rows) > 1:
            raise LocalPdfSourceBindingError(
                "local_pdf_document_source_duplicate"
            )
        if rows:
            if str(rows[0][1]) != serialized:
                raise LocalPdfSourceBindingError(
                    "local_pdf_document_source_conflict"
                )
            return {
                "status": "already_recorded",
                "document_id": int(document_id),
                "source_type": "local_pdf",
                "write_performed": False,
            }
        values: dict[str, Any] = {
            "document_id": int(document_id),
            "source_type": "local_pdf",
            "source_trace_json": serialized,
        }
        if "created_at" in columns:
            values["created_at"] = datetime.now(
                timezone.utc
            ).isoformat()
        if "source_sha256" in columns:
            values["source_sha256"] = binding.pdf_sha256
        if "source_revision_fingerprint" in columns:
            values["source_revision_fingerprint"] = (
                binding.source_revision_fingerprint
            )
        selected = list(values)
        connection.execute(
            f"INSERT INTO document_sources ({', '.join(selected)}) "
            f"VALUES ({', '.join('?' for _ in selected)})",
            [values[field] for field in selected],
        )
        connection.commit()
    return {
        "status": "recorded",
        "document_id": int(document_id),
        "source_type": "local_pdf",
        "write_performed": True,
    }


def verify_document_source(
    *,
    db_path: Path,
    data_dir: Path,
    document_id: int,
    binding: LocalPdfSourceBinding,
) -> dict[str, Any]:
    expected_trace = binding.source_trace()
    verify_managed_pdf(data_dir=data_dir, binding=binding)
    with sqlite3.connect(
        f"file:{Path(db_path).resolve().as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT source_type, source_trace_json
            FROM document_sources
            WHERE document_id = ?
            ORDER BY rowid
            """,
            (int(document_id),),
        ).fetchall()
    if len(rows) != 1 or str(rows[0]["source_type"]) != "local_pdf":
        raise LocalPdfSourceBindingError(
            "local_pdf_document_source_count_invalid"
        )
    try:
        trace = json.loads(str(rows[0]["source_trace_json"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise LocalPdfSourceBindingError(
            "local_pdf_document_source_trace_invalid"
        ) from exc
    if trace != expected_trace:
        raise LocalPdfSourceBindingError(
            "local_pdf_document_source_trace_mismatch"
        )
    return {
        "status": "verified",
        "document_id": int(document_id),
        "source_type": "local_pdf",
        "source_binding_count": 1,
        "pdf_sha256": binding.pdf_sha256,
        "write_performed": False,
    }


def verify_managed_pdf(
    *,
    data_dir: Path,
    binding: LocalPdfSourceBinding,
) -> dict[str, Any]:
    managed_pdf = _managed_pdf_path(data_dir, binding)
    if not managed_pdf.is_file():
        raise LocalPdfSourceBindingError(
            "local_pdf_managed_pdf_missing"
        )
    if _sha256_file(managed_pdf) != binding.pdf_sha256:
        raise LocalPdfSourceBindingError(
            "local_pdf_managed_pdf_sha_mismatch"
        )
    return {
        "status": "verified",
        "pdf_sha256": binding.pdf_sha256,
        "write_performed": False,
    }


def _managed_pdf_path(
    data_dir: Path,
    binding: LocalPdfSourceBinding,
) -> Path:
    _validate_safe_relative_path(binding.managed_pdf_relative_path)
    root = Path(data_dir).resolve(strict=False)
    candidate = (
        root / Path(PurePosixPath(binding.managed_pdf_relative_path))
    ).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LocalPdfSourceBindingError(
            "local_pdf_managed_pdf_path_unsafe"
        ) from exc
    return candidate


def _validate_safe_relative_path(value: str) -> None:
    cleaned = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(cleaned)
    if (
        not cleaned
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", cleaned)
    ):
        raise LocalPdfSourceBindingError(
            "local_pdf_managed_pdf_path_unsafe"
        )


def _require_sha(value: str, code: str) -> None:
    if not _SHA256_RE.fullmatch(str(value or "")):
        raise LocalPdfSourceBindingError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }

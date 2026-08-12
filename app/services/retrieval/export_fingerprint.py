from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.retrieval.evidence_loader import EvidenceRecord


EXPORT_SCHEMA_VERSION = "retrieval_evidence_export.v1"


def build_export_fingerprint(
    records: list[EvidenceRecord],
    *,
    source_index_hash: str,
    source_manifest_hash: str,
    query: str | None,
    retrieval_mode: str | None,
    options: dict[str, Any],
    export_format: str,
) -> str:
    payload = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "source_index_hash": source_index_hash,
        "source_manifest_hash": source_manifest_hash,
        "query": query,
        "retrieval_mode": retrieval_mode,
        "format": export_format,
        "options": options,
        "evidence": [
            {
                "selected_order": index + 1,
                "fragment_id": record.fragment_id,
                "content_hash": record.content_hash,
            }
            for index, record in enumerate(records)
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()

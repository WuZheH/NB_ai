from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = str(PROJECT_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != RUNTIME_ROOT]
sys.path.insert(0, RUNTIME_ROOT)

from app.services.zotero_retrieval_sync_service import (  # noqa: E402
    ZoteroRetrievalSyncError,
    sync_zotero_retrieval_generation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize one pinned Zotero source into a READ retrieval generation."
    )
    parser.add_argument(
        "--require-pdf-document",
        action="append",
        default=[],
        metavar="DOCUMENT_ID:CHUNK_COUNT",
    )
    parser.add_argument(
        "--forbid-pdf-page",
        action="append",
        default=[],
        metavar="DOCUMENT_ID:PDF_PAGE",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        required = dict(
            _parse_pair(value, label="required PDF document")
            for value in arguments.require_pdf_document
        )
        forbidden = [
            _parse_pair(value, label="forbidden PDF page")
            for value in arguments.forbid_pdf_page
        ]
        result = sync_zotero_retrieval_generation(
            required_pdf_documents=required,
            forbidden_pdf_pages=forbidden,
        )
    except ZoteroRetrievalSyncError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "production_db_write_performed": False,
                    "zotero_db_write_performed": False,
                    "pdf_passage_embedding_inference_count": 0,
                    "pdf_passage_vector_rebuild": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": "zotero_retrieval_sync_failed",
                    "details": {"cause_type": type(exc).__name__},
                    "production_db_write_performed": False,
                    "zotero_db_write_performed": False,
                    "pdf_passage_embedding_inference_count": 0,
                    "pdf_passage_vector_rebuild": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _parse_pair(value: str, *, label: str) -> tuple[int, int]:
    parts = str(value or "").split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"invalid {label}")
    left, right = (int(part) for part in parts)
    if left <= 0 or right <= 0:
        raise ValueError(f"invalid {label}")
    return left, right


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.retrieval.source_registry import (
    ALL_SOURCE_TYPES,
    RetrievalSourceRegistry,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect unified retrieval fragments without writing source data.",
    )
    parser.add_argument("--document-id", type=int, action="append")
    parser.add_argument("--source-type", choices=ALL_SOURCE_TYPES, action="append")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    result = RetrievalSourceRegistry().read(
        source_types=args.source_type,
        document_ids=args.document_id,
        limit=args.limit,
    )
    payload: dict[str, Any] = {"summary": result.to_summary()}
    if not args.summary_only:
        payload["fragments"] = [
            fragment.model_dump(mode="json")
            for fragment in result.fragments
        ]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.retrieval.fts_status_service import get_index_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Check derived retrieval FTS index status.")
    parser.add_argument("--index-path")
    parser.add_argument("--manifest-path")
    parser.add_argument("--aliases-path")
    args = parser.parse_args()
    kwargs = {
        key: value
        for key, value in {
            "index_path": args.index_path,
            "manifest_path": args.manifest_path,
            "query_aliases_path": args.aliases_path,
        }.items()
        if value is not None
    }
    result = get_index_status(**kwargs)
    _print_json(result)
    return 0 if result.get("status") == "ready" else 1


def _print_json(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

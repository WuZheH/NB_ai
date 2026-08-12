from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.retrieval.fts_search_service import search_retrieval


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the derived local retrieval FTS index.")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=("precision", "coverage"), default="precision")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--no-collapse-duplicates", action="store_true")
    parser.add_argument("--no-context", action="store_true")
    parser.add_argument("--filters-json", default="{}")
    parser.add_argument("--index-path")
    parser.add_argument("--manifest-path")
    parser.add_argument("--aliases-path")
    args = parser.parse_args()

    filters = json.loads(args.filters_json)
    request = {
        "query": args.query,
        "mode": args.mode,
        "limit": args.limit,
        "offset": args.offset,
        "collapse_duplicates": not args.no_collapse_duplicates,
        "include_context": not args.no_context,
        "filters": filters,
    }
    path_kwargs = {
        key: value
        for key, value in {
            "index_path": args.index_path,
            "manifest_path": args.manifest_path,
            "query_aliases_path": args.aliases_path,
        }.items()
        if value is not None
    }
    result = search_retrieval(request, **path_kwargs)
    _print_json(result)
    return 0


def _print_json(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

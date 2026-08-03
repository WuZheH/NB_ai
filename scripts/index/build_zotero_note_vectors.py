from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domains.retrieval.note_vector_index import build_zotero_note_vectors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the derived Qwen vector index for NOTEBOOK_AI Zotero user notes."
    )
    parser.add_argument("--index-dir", type=Path, default=None)
    args = parser.parse_args()
    kwargs = {"index_dir": args.index_dir} if args.index_dir is not None else {}
    _print_json(build_zotero_note_vectors(**kwargs))
    return 0


def _print_json(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

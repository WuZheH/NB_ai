from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.chapter_review_pipeline_service import ensure_note_classification_review_tables


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase7C dry-run-first note classification review schema migration helper."
    )
    parser.add_argument("--db-path", default="data/db/research_memory.db")
    parser.add_argument("--execute", action="store_true", help="Create classification review tables. Omit for dry-run.")
    args = parser.parse_args()
    result = ensure_note_classification_review_tables(
        research_db_path=Path(args.db_path),
        execute=args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

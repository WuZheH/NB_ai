from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.library.document_deletion_service import (
    DeletionRuntime,
    retry_incomplete_cleanup,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or retry post-commit Search book deletion cleanup.",
    )
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    runtime = DeletionRuntime(archive_root=arguments.archive_root) if arguments.archive_root else DeletionRuntime()
    result = retry_incomplete_cleanup(
        arguments.audit_id,
        apply=bool(arguments.apply),
        runtime=runtime,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"ready_to_retry", "completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

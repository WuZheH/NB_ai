from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import vector_store_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally sync NOTEBOOK_AI local LanceDB vector store.")
    parser.add_argument("--kind", choices=["all", "passages", "objects"], default="all")
    parser.add_argument("--model-path", default=vector_store_service.EMBEDDING_MODEL_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete-orphans", action="store_true")
    parser.add_argument("--no-delete-orphans", action="store_true")
    parser.add_argument("--rebuild-if-schema-mismatch", action="store_true")
    parser.add_argument("--store-path", type=Path, default=None)
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    delete_orphans = bool(args.delete_orphans and not args.no_delete_orphans)
    store_path = args.store_path or vector_store_service.LANCEDB_DIR
    manifest_path = args.manifest_path or vector_store_service.MANIFEST_PATH
    if args.source_id:
        if args.kind != "passages":
            parser.error("--source-id is supported only with --kind passages")
        if args.rebuild_if_schema_mismatch:
            parser.error("--source-id affected-only sync does not allow full rebuild")
        if args.delete_orphans:
            parser.error("--source-id affected-only sync does not delete orphan vectors")
        if args.apply and args.dry_run:
            parser.error("choose either --dry-run or --apply for --source-id sync")
        result = vector_store_service.sync_affected_passage_embeddings(
            args.source_id,
            dry_run=not args.apply,
            apply=args.apply,
            store_path=store_path,
            manifest_path=manifest_path,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("scope:", result["scope"], "dry_run:", result["dry_run"], "full_rebuild_allowed:", result["full_rebuild_allowed"])
            for item in result["items"]:
                print(
                    "source_id=", item["source_id"],
                    "exists_in_db=", item["exists_in_db"],
                    "exists_in_lancedb=", item["exists_in_lancedb"],
                    "source_hash_current=", item["source_hash_current"],
                    "source_hash_indexed=", item["source_hash_indexed"],
                    "status=", item["status"],
                    "planned_action=", item["planned_action"],
                )
        return 0
    rebuild_if_schema_mismatch = True if not args.dry_run else bool(args.rebuild_if_schema_mismatch)
    print("backend:", vector_store_service.BACKEND)
    print("model:", vector_store_service.EMBEDDING_MODEL)
    print("model_path:", args.model_path)
    print("store:", store_path)
    print("manifest:", manifest_path)
    print("dry_run:", args.dry_run, "delete_orphans:", delete_orphans, "rebuild_if_schema_mismatch:", rebuild_if_schema_mismatch)

    inspection = vector_store_service.inspect_vector_store_schema(store_path=store_path, manifest_path=manifest_path)
    print("tables:", inspection["table_names"])
    for label in ("passages", "objects"):
        section = inspection[label]
        print(
            f"{label}_schema",
            "exists=", section["exists"],
            "schema_upgrade=", section["schema_upgrade"],
            "missing_fields=", section["missing_fields"],
            "extra_fields=", section["extra_fields"],
        )

    results = vector_store_service.sync_vector_store(
        args.kind,
        limit=args.limit,
        dry_run=args.dry_run,
        delete_orphans=delete_orphans,
        rebuild_if_schema_mismatch=rebuild_if_schema_mismatch,
        store_path=store_path,
        manifest_path=manifest_path,
    )
    for result in results:
        print(
            result["kind"],
            "scanned=", result["scanned_count"],
            "inserted=", result["inserted_count"],
            "updated=", result["updated_count"],
            "skipped=", result["skipped_count"],
            "orphans=", result["orphan_count"],
            "deleted_orphans=", result["deleted_orphan_count"],
            "would_insert=", result["would_insert"],
            "would_update=", result["would_update"],
            "would_delete=", result["would_delete"],
            "schema_upgrade=", result["schema_needs_upgrade"],
            "schema_missing_fields=", result["schema_missing_fields"],
            "backup_path=", result["backup_path"] or result["planned_backup_path"],
            "rebuilt_table=", result["rebuilt_table"],
            "elapsed_ms=", result["elapsed_ms"],
        )

    status = vector_store_service.check_vector_store_status(store_path=store_path, manifest_path=manifest_path)
    print("available:", status["available"], "stale:", status["stale"], "reason:", status["reason"])
    print("sync:", status.get("sync"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

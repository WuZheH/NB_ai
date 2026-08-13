from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import vector_store_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Build NOTEBOOK_AI local LanceDB vector store.")
    parser.add_argument("--kind", choices=["all", "passages", "objects"], default="all")
    parser.add_argument("--model-path", default=vector_store_service.EMBEDDING_MODEL_PATH)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    print("backend:", vector_store_service.BACKEND)
    print("model:", vector_store_service.EMBEDDING_MODEL)
    print("model_path:", args.model_path)
    print("store:", vector_store_service.LANCEDB_DIR)
    print("manifest:", vector_store_service.MANIFEST_PATH)

    results = []
    if args.kind in {"all", "objects"}:
        result = vector_store_service.build_object_embeddings(
            model_path=args.model_path,
            reset=args.reset,
            limit=args.limit,
        )
        results.append(result)
        print("objects:", result["count"], "dim:", result["embedding_dim"], "elapsed_ms:", result["elapsed_ms"])

    if args.kind in {"all", "passages"}:
        result = vector_store_service.build_passage_embeddings(
            model_path=args.model_path,
            reset=args.reset,
            limit=args.limit,
        )
        results.append(result)
        print("passages:", result["count"], "dim:", result["embedding_dim"], "elapsed_ms:", result["elapsed_ms"])

    status = vector_store_service.check_vector_store_status()
    print("available:", status["available"], "stale:", status["stale"], "reason:", status["reason"])
    print("tables:", status["tables"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

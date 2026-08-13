from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime.machine_config import load_machine_config, write_machine_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="configure-search-machine")
    parser.add_argument("action", choices=("inspect", "validate", "set"))
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--embedding-model-path")
    parser.add_argument("--reranker-model-path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config_path = Path(arguments.config_path)
    if arguments.action == "set":
        if not arguments.embedding_model_path or not arguments.reranker_model_path:
            return _emit({"status": "error", "error_code": "required_field_missing"}, 1)
        try:
            config = write_machine_config(
                config_path,
                embedding_model_path=arguments.embedding_model_path,
                reranker_model_path=arguments.reranker_model_path,
            )
        except RuntimeError as exc:
            return _emit({"status": "error", "error_code": str(exc)}, 1)
        return _emit({"status": "written", "machine_config": config.public_status()}, 0)
    config = load_machine_config(config_path)
    result = {"status": "ready" if config.ready else "unavailable", "machine_config": config.public_status()}
    if arguments.action == "validate" and not config.ready:
        return _emit(result, 1)
    return _emit(result, 0)


def _emit(value: dict, exit_code: int) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

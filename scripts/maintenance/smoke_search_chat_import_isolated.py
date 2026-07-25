from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


def main() -> int:
    arguments = _parser().parse_args()
    root = Path(arguments.root).resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("isolated_root_must_be_empty")
    data_dir = root / "data"
    inbox = root / "inbox"
    runtime_dir = root / "runtime"
    logs_dir = root / "logs"
    for path in (data_dir, inbox, runtime_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)

    os.environ["SEARCH_DATA_DIR"] = str(data_dir)
    os.environ["SEARCH_IMPORT_INBOX"] = str(inbox)
    os.environ["SEARCH_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["SEARCH_LOG_DIR"] = str(logs_dir)
    os.environ["SEARCH_MACHINE_CONFIG_PATH"] = str(root / "missing-machine-config.json")
    os.environ["NOTEBOOK_AI_VECTOR_STORE_WORKER_ENABLED"] = "0"
    os.environ["NOTEBOOK_AI_VECTOR_STORE_AUTO_SYNC_ENABLED"] = "0"
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from app.db.init_db import init_db
    from app.services import chat_tool_service
    from app.services.pdf_backend_service import load_fitz_backend

    init_db()
    source = inbox / "isolated-chat-import.pdf"
    fitz = load_fitz_backend()
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Isolated chat import evidence for motion diffusion.")
    document.set_metadata({"title": "Isolated Chat Import Fixture"})
    document.save(source)
    document.close()

    runtime = chat_tool_service.ChatToolRuntime(
        db_path=data_dir / "db" / "research_memory.db",
        data_dir=data_dir,
        inbox_root=inbox,
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    if preview.get("duplicate_status") != "not_detected":
        raise RuntimeError("first_preview_unexpected_duplicate")
    imported = chat_tool_service.import_document(
        confirmation_token=str(preview["confirmation_token"]),
        confirmed=True,
        runtime=runtime,
    )
    document_id = int(imported["document_id"])
    with sqlite3.connect(f"{runtime.db_path.as_uri()}?mode=ro", uri=True) as connection:
        document_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()[0]
        )
        chunk_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0]
        )
    duplicate = chat_tool_service.import_preview(runtime=runtime)
    managed_pdfs = list((data_dir / "pdfs" / "chat_imports").glob("*.pdf"))
    result = {
        "status": "ok",
        "document_id": document_id,
        "document_count": document_count,
        "chunk_count": chunk_count,
        "duplicate_status": duplicate.get("duplicate_status"),
        "duplicate_token_absent": duplicate.get("confirmation_token") is None,
        "source_pdf_preserved": source.is_file(),
        "managed_pdf_count": len(managed_pdfs),
        "production_path_used": False,
    }
    if (
        document_count != 1
        or chunk_count < 1
        or result["duplicate_status"] != "duplicate"
        or not result["duplicate_token_absent"]
        or not result["source_pdf_preserved"]
        or len(managed_pdfs) != 1
    ):
        raise RuntimeError("isolated_import_contract_failed")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import pytest

from app.core.paths import DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH
from app.services.retrieval.fts_status_service import get_index_status


ACCEPTED_FORMAL_STATUSES = {"ready"}


def test_production_fts_status_is_read_only_and_accepted() -> None:
    if not all(path.is_file() for path in (DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH)):
        pytest.skip("production database and FTS artifacts are intentionally absent")
    status = get_index_status()

    assert status["status"] in ACCEPTED_FORMAL_STATUSES
    assert status["ready"] is True
    assert status["validation"]["valid"] is True
    assert status["validation"]["integrity_check"] == "ok"
    for flag in (
        "db_write_performed",
        "production_db_write_performed",
        "zotero_db_write_performed",
        "vector_write_performed",
        "llm_called",
    ):
        assert status[flag] is False


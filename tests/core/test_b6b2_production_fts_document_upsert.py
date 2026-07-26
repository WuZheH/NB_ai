import pytest

from app.services.retrieval import fts_index_service


def test_production_fts_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="explicit opt-in"):
        fts_index_service.upsert_document_retrieval_fts(
            document_id=1,
            index_path=fts_index_service.DEFAULT_INDEX_PATH,
            manifest_path=fts_index_service.DEFAULT_MANIFEST_PATH,
            research_db_path=fts_index_service.DEFAULT_DB_PATH,
        )

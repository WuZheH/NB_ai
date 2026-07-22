from sqlalchemy import create_engine, inspect

from app.db.session import Base
from app import models as _models  # noqa: F401


def test_fresh_sqlite_schema_creates_object_candidate_import_job_index_once() -> None:
    engine = create_engine("sqlite:///:memory:")

    try:
        Base.metadata.create_all(bind=engine)
        index_names = {
            index["name"]
            for index in inspect(engine).get_indexes("object_candidates")
        }
    finally:
        engine.dispose()

    assert "ix_object_candidates_import_job_id" in index_names

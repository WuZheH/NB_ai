import asyncio

from sqlalchemy import create_engine, inspect

from app import main as main_module
from app.db.session import Base
from app import models as _models  # noqa: F401
from app.db import init_db as init_db_module


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


def test_empty_database_is_initialized_for_runtime_startup(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(init_db_module, "engine", engine)

    try:
        assert init_db_module.initialize_database_if_empty() is True
        assert "documents" in inspect(engine).get_table_names()
        assert init_db_module.initialize_database_if_empty() is False
    finally:
        engine.dispose()


def test_existing_database_is_not_changed_by_runtime_startup(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE existing_data (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(init_db_module, "engine", engine)

    try:
        assert init_db_module.initialize_database_if_empty() is False
        assert inspect(engine).get_table_names() == ["existing_data"]
    finally:
        engine.dispose()


def test_lifespan_initializes_empty_database_before_worker(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        main_module,
        "initialize_database_if_empty",
        lambda: events.append("initialize_database"),
    )
    monkeypatch.setattr(
        main_module,
        "start_vector_store_worker",
        lambda **_kwargs: events.append("start_worker"),
    )

    async def stop_worker() -> None:
        events.append("stop_worker")

    monkeypatch.setattr(main_module, "stop_vector_store_worker", stop_worker)

    async def exercise_lifespan() -> None:
        async with main_module.lifespan(main_module.app):
            events.append("yield")

    asyncio.run(exercise_lifespan())

    assert events == ["initialize_database", "start_worker", "yield", "stop_worker"]

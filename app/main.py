from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.evidence_export_api import router as evidence_export_router
from app.api.import_api import router as import_router
from app.api.library_api import router as library_router
from app.api.product_api import router as product_router
from app.api.retrieval_api import router as retrieval_router
from app.api.search_api import router as search_router
from app.api.zotero_api import configure_production_connection_factories, router as zotero_router
from app.core.config import settings
from app.services.vector_store_worker import start_vector_store_worker, stop_vector_store_worker


API_ROUTERS = (
    product_router,
    library_router,
    search_router,
    retrieval_router,
    evidence_export_router,
    import_router,
    zotero_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_vector_store_worker(
        enabled=settings.vector_store_worker_enabled,
        auto_sync_enabled=settings.vector_store_auto_sync_enabled,
    )
    try:
        yield
    finally:
        await stop_vector_store_worker()


def create_app() -> FastAPI:
    configure_production_connection_factories(
        settings.sqlite_db_path,
        enable_mechanism_draft_candidate_writes=False,
    )
    app = FastAPI(
        title="Search Local Product API",
        version="16A",
        description="Local-first product API shell for safe read and dry-run workflows.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
            "Content-Type",
        ],
    )
    for router in API_ROUTERS:
        app.include_router(router)
    return app


app = create_app()

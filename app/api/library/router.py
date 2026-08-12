from __future__ import annotations

from fastapi import APIRouter

from app.api.library import (
    books,
    documents,
    evidence,
    importing,
    management,
    pdf,
    search,
)


router = APIRouter(prefix="/api/v1/library")

for subrouter in (
    importing.router,
    management.router,
    books.router,
    search.router,
    pdf.router,
    documents.router,
    evidence.router,
):
    router.include_router(subrouter)

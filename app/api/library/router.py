from __future__ import annotations

from fastapi import APIRouter

from app.api.library import (
    chapters,
    documents,
    evidence,
    importing,
    mechanisms,
    objects,
    pdf,
    review,
    search,
)


router = APIRouter(prefix="/api/v1/library")

for subrouter in (
    importing.router,
    chapters.router,
    mechanisms.router,
    review.router,
    objects.router,
    search.router,
    pdf.router,
    documents.router,
    evidence.router,
):
    router.include_router(subrouter)

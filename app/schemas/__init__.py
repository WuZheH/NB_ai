"""Schema package."""

from app.schemas.book import (
    BookChapterProgress,
    BookChapterRead,
    BookDetailRead,
    BookNextObjectImportChapterRead,
)

__all__ = [
    "BookChapterProgress",
    "BookChapterRead",
    "BookDetailRead",
    "BookNextObjectImportChapterRead",
]

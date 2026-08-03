"""High-quality NOTEBOOK_AI retrieval domain."""

from typing import Any


def search_notebook(request: Any) -> dict[str, Any]:
    from app.domains.retrieval.notebook_search_service import search_notebook as _search

    return _search(request)

__all__ = ["search_notebook"]

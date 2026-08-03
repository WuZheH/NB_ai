from __future__ import annotations

from app.services import library_core_service as _core


def search_library(*args, **kwargs):
    return _core.search_library(*args, **kwargs)


def search_library_grouped(*args, **kwargs):
    return _core.search_library_grouped(*args, **kwargs)


def normalize_grouped_search_query(*args, **kwargs):
    return _core.normalize_grouped_search_query(*args, **kwargs)

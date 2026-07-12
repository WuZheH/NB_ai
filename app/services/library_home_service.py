from __future__ import annotations

from app.services import library_core_service as _core


def get_library_home(*args, **kwargs):
    return _core.get_library_home(*args, **kwargs)


def list_read_books(*args, **kwargs):
    return _core.list_read_books(*args, **kwargs)

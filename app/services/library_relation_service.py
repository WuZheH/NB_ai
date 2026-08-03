from __future__ import annotations

from app.services import library_core_service as _core


def show_library_notes(*args, **kwargs):
    return _core.show_library_notes(*args, **kwargs)


def show_library_note(*args, **kwargs):
    return _core.show_library_note(*args, **kwargs)

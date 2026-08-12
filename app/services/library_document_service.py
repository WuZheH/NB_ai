from __future__ import annotations

from app.services import library_core_service as _core


def show_library_document(*args, **kwargs):
    return _core.show_library_document(*args, **kwargs)


def get_document_pdf_source(*args, **kwargs):
    return _core.get_document_pdf_source(*args, **kwargs)


def resolve_document_pdf_path(*args, **kwargs):
    return _core.resolve_document_pdf_path(*args, **kwargs)


def resolve_safe_pdf_path(*args, **kwargs):
    return _core.resolve_safe_pdf_path(*args, **kwargs)


def is_safe_pdf_path(*args, **kwargs):
    return _core.is_safe_pdf_path(*args, **kwargs)


def is_test_library_record(*args, **kwargs):
    return _core.is_test_library_record(*args, **kwargs)

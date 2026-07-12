from __future__ import annotations

from app.services import library_core_service as _core


def normalize_evidence_text(*args, **kwargs):
    return _core.normalize_evidence_text(*args, **kwargs)


def is_metadata_chunk_text(*args, **kwargs):
    return _core.is_metadata_chunk_text(*args, **kwargs)


def is_metadata_chunk(*args, **kwargs):
    return _core.is_metadata_chunk(*args, **kwargs)


def evidence_locator_contract(*args, **kwargs):
    return _core.evidence_locator_contract(*args, **kwargs)


def show_library_evidence(*args, **kwargs):
    return _core.show_library_evidence(*args, **kwargs)


def show_library_chunk(*args, **kwargs):
    return _core.show_library_chunk(*args, **kwargs)

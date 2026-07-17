from __future__ import annotations

import hashlib
import inspect
import json

from app.api import library_api
from app.api.library import router as library_router_module
from app.api.library.chapters import get_book_detail
from app.api.library.importing import classify_pdf_import
from app.api.library.search import search_high_quality
from app.core.paths import DEFAULT_DB_PATH
from app.domains.chapter_review import _pipeline_legacy, _prompt_legacy
from app.domains.database_search import _legacy as database_search_legacy
from app.domains.library import _legacy as library_legacy
from app.services import (
    chapter_note_correction_prompt_service,
    chapter_review_pipeline_service,
    database_search_service,
    library_core_service,
    library_service,
)


EXPECTED_PUBLIC_SYMBOL_COUNT = 125
EXPECTED_PUBLIC_SYMBOL_FINGERPRINT = (
    "f891e80e57e4b4ec84c8d3d8bce9cb3cf95043a5616971746230daccfb1530ea"
)
EXPECTED_SERVICE_CONTRACTS = {
    "app.services.chapter_review_pipeline_service": (
        149,
        "67ee7a2ca731c07e346a2a30306f1a7b11445b1c1edb4ad4a0b8b4ba8a59d37b",
        68,
        "624f70aa8029610e5bc3d27dbfff903427eb33063d77e2f3216e78e1316e9def",
    ),
    "app.services.chapter_note_correction_prompt_service": (
        52,
        "a81c3f079a79ad1ddc860f910bca780a933729f14f5f6969ea704c5acf39c8b1",
        18,
        "ff786aecda8f7c2c8d5b24e5f5f6125c9b88105d95acfb16a3e367f3f2dfadbd",
    ),
    "app.services.database_search_service": (
        20,
        "9d32e20271e6147ade33eda4bc88a3e55c5285c95fa17acb890ac129609db872",
        1,
        "d5ad44089142c9dd167df7f0f30438ba44b7eac85c6b320ee2823484be9a43b2",
    ),
    "app.services.library_core_service": (
        77,
        "af79acba93515e560e41c93121ceef411b7183efd3e2525d86efb108448b60ba",
        33,
        "fce6bc2d41a3e8d29f42c693e8aec535424c442bd74555eb676ee13dd761abbb",
    ),
}


def test_library_api_facade_preserves_router_and_endpoint_imports() -> None:
    assert library_api.router is library_router_module.router
    assert library_api.classify_pdf_import is classify_pdf_import
    assert library_api.get_book_detail is get_book_detail
    assert library_api.search_high_quality is search_high_quality


def test_library_api_facade_preserves_the_public_symbol_surface() -> None:
    names = sorted(name for name in vars(library_api) if not name.startswith("_"))
    fingerprint = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()

    assert len(names) == EXPECTED_PUBLIC_SYMBOL_COUNT
    assert fingerprint == EXPECTED_PUBLIC_SYMBOL_FINGERPRINT


def _owned_callable_contract(module) -> list[tuple[str, str, str]]:
    contract: list[tuple[str, str, str]] = []
    for name, value in vars(module).items():
        if name.startswith("_") or getattr(value, "__module__", None) != module.__name__:
            continue
        if not callable(value):
            continue
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            signature = "<unavailable>"
        signature = signature.replace(
            repr(DEFAULT_DB_PATH),
            "Path('<SEARCH_DATA_DIR>/db/research_memory.db')",
        )
        contract.append((name, signature, type(value).__name__))
    return sorted(contract)


def test_backend_service_facades_preserve_public_names_and_signatures() -> None:
    modules = (
        chapter_review_pipeline_service,
        chapter_note_correction_prompt_service,
        database_search_service,
        library_core_service,
    )
    for module in modules:
        expected_public_count, expected_public_hash, expected_owned_count, expected_owned_hash = (
            EXPECTED_SERVICE_CONTRACTS[module.__name__]
        )
        public_names = sorted(name for name in vars(module) if not name.startswith("_"))
        owned_contract = _owned_callable_contract(module)

        assert len(public_names) == expected_public_count
        assert hashlib.sha256("\n".join(public_names).encode("utf-8")).hexdigest() == (
            expected_public_hash
        )
        assert len(owned_contract) == expected_owned_count
        encoded = json.dumps(
            owned_contract,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert hashlib.sha256(encoded).hexdigest() == expected_owned_hash


def test_backend_service_facades_reexport_the_internal_implementations() -> None:
    assert (
        chapter_review_pipeline_service.save_chapter_note_correction_review
        is _pipeline_legacy.save_chapter_note_correction_review
    )
    assert (
        chapter_note_correction_prompt_service.validate_chapter_note_correction_review
        is _prompt_legacy.validate_chapter_note_correction_review
    )
    assert database_search_service.build_database_search is database_search_legacy.build_database_search
    assert library_core_service.search_library is library_legacy.search_library
    assert library_service._core is library_core_service

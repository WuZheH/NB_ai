from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from app.api import library_api
from app.api.library import router as library_router_module
from app.api.library.books import get_book_detail
from app.api.library.importing import classify_pdf_import
from app.api.library.search import search_high_quality
from app.core.paths import DEFAULT_DB_PATH
from app.domains.chapter_review import _pipeline_legacy, _prompt_legacy
from app.domains.library import _legacy as library_legacy
from app.services import (
    chapter_note_correction_prompt_service,
    chapter_review_pipeline_service,
    library_core_service,
    library_service,
)


ROOT = Path(__file__).resolve().parents[2]


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


def test_library_api_facade_exposes_only_canonical_product_surfaces() -> None:
    names = {name for name in vars(library_api) if not name.startswith("_")}
    for expected in (
        "router",
        "get_book_detail",
        "get_book_chapter_workspace_state",
        "read_shelf",
        "search_high_quality",
        "document_detail",
        "evidence_detail",
        "document_pdf",
        "classify_pdf_import",
    ):
        assert expected in names
    for obsolete in (
        "save_book_chapter_note_correction_review",
        "get_book_chapter_note_classification_package",
        "get_book_chapter_object_candidates_dry_run",
        "preview_workspace_selection_source_pack",
    ):
        assert obsolete not in names


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
    assert library_core_service.search_library is library_legacy.search_library
    assert library_service._core is library_core_service


def test_obsolete_library_domain_facades_are_removed() -> None:
    obsolete_paths = (
        "app/domains/library/evidence.py",
        "app/domains/library/home.py",
        "app/domains/library/notes.py",
        "app/domains/library/search.py",
        "app/domains/library/service.py",
    )
    for relative in obsolete_paths:
        assert not (ROOT / relative).exists()

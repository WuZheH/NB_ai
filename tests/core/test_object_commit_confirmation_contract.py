from __future__ import annotations

from app.api import import_api


def test_commit_objects_endpoints_require_strict_confirmation_context() -> None:
    source = import_api.__file__
    text = open(source, encoding="utf-8").read()
    commit_objects_block = text[
        text.index('"/{import_job_id}/commit-objects"') : text.index(
            '"/{import_job_id}/commit-reviewed-objects"'
        )
    ]
    commit_reviewed_block = text[
        text.index('"/{import_job_id}/commit-reviewed-objects"') : text.index(
            '"/{import_job_id}/remap-reviewed-objects-preview"'
        )
    ]
    assert 'strict_context=True' in commit_objects_block
    assert 'strict_context=True' in commit_reviewed_block


def test_strict_confirmation_rejects_wrong_context() -> None:
    blocked = import_api._commit_confirmation(
        type(
            "Request",
            (),
            {"confirm_write": True, "confirmation_context": "commit_objects_after_review"},
        )(),
        "commit_reviewed_objects_after_remap",
        strict_context=True,
    )
    assert blocked is not None
    assert blocked["status"] == "BLOCKED"


def test_strict_confirmation_rejects_missing_context() -> None:
    blocked = import_api._commit_confirmation(
        type(
            "Request",
            (),
            {"confirm_write": True, "confirmation_context": None},
        )(),
        "commit_objects_after_review",
        strict_context=True,
    )
    assert blocked is not None
    assert blocked["status"] == "BLOCKED"


def test_strict_confirmation_accepts_exact_context() -> None:
    blocked = import_api._commit_confirmation(
        type(
            "Request",
            (),
            {
                "confirm_write": True,
                "confirmation_context": "commit_reviewed_objects_after_remap",
            },
        )(),
        "commit_reviewed_objects_after_remap",
        strict_context=True,
    )
    assert blocked is None

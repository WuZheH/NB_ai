import pytest

from app.services.chat_pdf_production_import_service import import_document_to_production


def test_production_orchestrator_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="opt_in"):
        import_document_to_production(import_job_id="missing", document_type="paper")

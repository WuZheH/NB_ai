from app.services.chat_pdf_production_import_service import import_document_to_production


def test_production_orchestrator_is_explicit_and_callable():
    assert callable(import_document_to_production)

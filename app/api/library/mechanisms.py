from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.post("/manual-chatgpt-bridge/source-pack-preview")
def preview_workspace_selection_source_pack(
    request: WorkspaceSelectionSourcePackRequest,
) -> dict[str, Any]:
    payload = workspace_selection_source_pack_service.build_workspace_selection_source_pack_preview(
        document_id=request.document_id,
        chapter_id=request.chapter_id,
        chunk_id=request.chunk_id,
        server_note_id=request.server_note_id,
        client_note_id=request.client_note_id,
        object_candidate_ids=request.object_candidate_ids,
        reviewed_object_refs=request.reviewed_object_refs,
    )
    return {
        **payload,
        "implementation_status": "connected_read_only",
        "local_api_transport_used": True,
        "external_api_called": False,
    }


@router.post("/manual-chatgpt-bridge/prompt-export")
def export_mechanism_source_pack_prompt(
    request: MechanismSourcePackPromptExportRequest,
) -> dict[str, Any]:
    payload = mechanism_prompt_export_service.build_chatgpt_prompt_export_from_source_pack(
        request.source_pack_result,
        include_expected_schema=request.include_expected_schema,
        include_prompt_payload=request.include_prompt_payload,
        chapter_id=request.chapter_id,
        import_batch_id=request.import_batch_id,
    )
    return {
        **payload,
        "implementation_status": "connected_read_only",
        "local_api_transport_used": True,
        "external_api_called": False,
    }


@router.post("/manual-chatgpt-bridge/validate-pasteback")
def validate_mechanism_source_pack_pasteback(
    request: MechanismSourcePackPastebackValidateRequest,
) -> dict[str, Any]:
    payload = mechanism_prompt_export_service.validate_pasted_source_pack_chatgpt_response(
        request.source_pack_result,
        request.pasted_chatgpt_response_json,
    )
    return {
        **payload,
        "implementation_status": "connected_read_only",
        "local_api_transport_used": True,
        "external_api_called": False,
    }


@router.post("/mechanism-draft-review/packet-preview")
def preview_mechanism_draft_review_packet(
    request: MechanismDraftReviewPacketPreviewRequest,
) -> dict[str, Any]:
    payload = mechanism_draft_review_service.build_mechanism_draft_review_ui_packet(
        request.pasteback_validation_result,
        source_pack_result=request.source_pack_result,
    )
    return {
        **payload,
        "implementation_status": "connected_read_only",
        "local_api_transport_used": True,
        "external_api_called": False,
    }


@router.post("/mechanism-draft-review/action-preview")
def preview_mechanism_draft_review_action(
    request: MechanismDraftReviewActionPreviewRequest,
) -> dict[str, Any]:
    payload = mechanism_draft_review_service.preview_mechanism_draft_review_action(
        request.review_packet,
        action=request.action,
        review_notes=request.review_notes,
        merge_into_packet_id=request.merge_into_packet_id,
    )
    return {
        **payload,
        "implementation_status": "connected_read_only",
        "local_api_transport_used": True,
        "external_api_called": False,
    }

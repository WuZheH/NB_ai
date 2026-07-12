from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services import mechanism_draft_prompt_service


EXPORT_MODE = "manual_chatgpt_prompt"
USER_INSTRUCTIONS = [
    "Copy this prompt into ChatGPT.",
    "Paste the JSON response back into NOTEBOOK_AI.",
    "Do not edit the original note_text or selected_text fields.",
]


def build_readiness_report_from_mechanism_source_pack(
    source_pack_result: Mapping[str, Any],
) -> dict[str, Any]:
    pack = source_pack_result.get("mechanism_source_pack")
    if not isinstance(pack, Mapping):
        return {
            "readiness_status": "blocked",
            "object_review_required": False,
            "mechanism_prompt_payload_preview": None,
            "blockers": ["mechanism_source_pack_missing"],
            **_safety_flags(),
        }
    readiness = pack.get("readiness") if isinstance(pack.get("readiness"), Mapping) else {}
    blockers = list(readiness.get("missing") or [])
    if pack.get("source_mode") == "unknown":
        blockers.append("source_mode_unknown")
    payload = _prompt_payload_from_source_pack(pack)
    return {
        "readiness_status": "ready_for_mechanism_prompt" if not blockers else "blocked",
        "object_review_required": False,
        "mechanism_prompt_payload_preview": payload,
        "source_mechanism_source_pack": deepcopy(dict(pack)),
        "blockers": blockers,
        "warnings": list(pack.get("warnings") or []),
        **_safety_flags(),
    }


def build_chatgpt_prompt_export_from_source_pack(
    source_pack_result: Mapping[str, Any],
    *,
    include_expected_schema: bool = True,
    include_prompt_payload: bool = True,
    chapter_id: int | None = None,
    import_batch_id: str | None = None,
) -> dict[str, Any]:
    readiness_report = build_readiness_report_from_mechanism_source_pack(source_pack_result)
    package = build_chatgpt_prompt_export_package(
        readiness_report,
        include_expected_schema=include_expected_schema,
        include_prompt_payload=include_prompt_payload,
        chapter_id=chapter_id,
        import_batch_id=import_batch_id,
        binding_mode="mechanism_source_pack",
    )
    package["source_pack_readiness_context"] = readiness_report
    return package


def validate_pasted_source_pack_chatgpt_response(
    source_pack_result: Mapping[str, Any],
    pasted_json: Mapping[str, Any],
) -> dict[str, Any]:
    readiness_report = build_readiness_report_from_mechanism_source_pack(source_pack_result)
    return validate_pasted_chatgpt_response(readiness_report, pasted_json)


def build_chatgpt_prompt_export_package(
    readiness_report: Mapping[str, Any],
    *,
    include_expected_schema: bool = True,
    include_prompt_payload: bool = True,
    chapter_id: int | None = None,
    import_batch_id: str | None = None,
    binding_mode: str = "single_note",
) -> dict[str, Any]:
    prompt_result = mechanism_draft_prompt_service.build_mechanism_draft_prompt(
        readiness_report,
        include_prompt_text=True,
        include_expected_schema=include_expected_schema,
    )
    metadata = build_prompt_export_metadata(
        readiness_report,
        chapter_id=chapter_id,
        import_batch_id=import_batch_id,
        binding_mode=binding_mode,
    )
    if prompt_result["blocked"]:
        return _export_package(
            status="BLOCKED",
            binding_mode=binding_mode,
            metadata=metadata,
            blockers=[str(prompt_result.get("blocked_reason") or "prompt_build_blocked")],
        )
    payload = prompt_result["prompt_payload_json"]
    return _export_package(
        status="OK",
        binding_mode=binding_mode,
        metadata=metadata,
        copy_ready_prompt=_manual_workflow_header() + str(prompt_result["prompt_text"]),
        prompt_payload_json=payload if include_prompt_payload else None,
        expected_response_schema=prompt_result["expected_response_schema"],
        evidence_summary=_evidence_summary(payload),
        paste_back_readiness_context=deepcopy(dict(readiness_report)),
    )


def build_copy_ready_prompt_text(readiness_report: Mapping[str, Any]) -> str | None:
    package = build_chatgpt_prompt_export_package(readiness_report)
    return package["copy_ready_prompt"]


def build_expected_response_schema() -> dict[str, Any]:
    return mechanism_draft_prompt_service.mechanism_draft_expected_response_schema()


def build_prompt_export_metadata(
    readiness_report: Mapping[str, Any],
    *,
    chapter_id: int | None = None,
    import_batch_id: str | None = None,
    binding_mode: str = "single_note",
) -> dict[str, Any]:
    payload = readiness_report.get("mechanism_prompt_payload_preview") or {}
    note_ids = _source_note_ids(payload)
    return {
        "binding_mode": binding_mode,
        "bound_inspiration_note_ids": note_ids,
        "chapter_id": chapter_id,
        "import_batch_id": import_batch_id,
        "manual_copy_paste_required": True,
        "merge_selected_by_user": binding_mode == "explicit_note_group",
    }


def build_chatgpt_prompt_batch_export(
    readiness_reports: list[Mapping[str, Any]],
    *,
    chapter_id: int | None = None,
    import_batch_id: str | None = None,
    merge_selected_by_user: bool = False,
    include_expected_schema: bool = True,
    include_prompt_payload: bool = True,
) -> dict[str, Any]:
    if merge_selected_by_user and len(readiness_reports) > 1:
        grouped_report, blockers = _explicit_group_readiness_context(readiness_reports)
        if blockers:
            packages = [
                _export_package(
                    status="BLOCKED",
                    binding_mode="explicit_note_group",
                    metadata={
                        "binding_mode": "explicit_note_group",
                        "bound_inspiration_note_ids": [],
                        "chapter_id": chapter_id,
                        "import_batch_id": import_batch_id,
                        "manual_copy_paste_required": True,
                        "merge_selected_by_user": True,
                    },
                    blockers=blockers,
                )
            ]
        else:
            packages = [
                build_chatgpt_prompt_export_package(
                    grouped_report,
                    include_expected_schema=include_expected_schema,
                    include_prompt_payload=include_prompt_payload,
                    chapter_id=chapter_id,
                    import_batch_id=import_batch_id,
                    binding_mode="explicit_note_group",
                )
            ]
        return _batch_result(
            packages,
            batch_mode="explicit_note_group",
            chapter_id=chapter_id,
            import_batch_id=import_batch_id,
        )

    packages = [
        build_chatgpt_prompt_export_package(
            report,
            include_expected_schema=include_expected_schema,
            include_prompt_payload=include_prompt_payload,
            chapter_id=chapter_id,
            import_batch_id=import_batch_id,
            binding_mode="single_note",
        )
        for report in readiness_reports
    ]
    return _batch_result(
        packages,
        batch_mode="one_note_per_prompt",
        chapter_id=chapter_id,
        import_batch_id=import_batch_id,
    )


def validate_pasted_chatgpt_response(
    readiness_report: Mapping[str, Any],
    pasted_json: Mapping[str, Any],
) -> dict[str, Any]:
    validation = mechanism_draft_prompt_service.validate_mechanism_draft_response(
        pasted_json,
        readiness_report,
    )
    if validation["blocked"]:
        status = "BLOCKED"
    elif validation["is_valid"]:
        status = "OK"
    else:
        status = "INVALID"
    pending_preview = (
        package_validated_mechanism_draft(readiness_report, pasted_json)
        if validation["is_valid"] and not validation["blocked"]
        else None
    )
    return {
        "status": status,
        "validator_passed": bool(validation["is_valid"] and not validation["blocked"]),
        "validation_report": validation,
        "pending_draft_preview": pending_preview,
        **_safety_flags(),
    }


def validate_pasted_mechanism_draft_json(
    readiness_report: Mapping[str, Any],
    pasted_json: Mapping[str, Any],
) -> dict[str, Any]:
    return validate_pasted_chatgpt_response(readiness_report, pasted_json)


def package_validated_mechanism_draft(
    readiness_report: Mapping[str, Any],
    validated_response: Mapping[str, Any],
) -> dict[str, Any] | None:
    validation = mechanism_draft_prompt_service.validate_mechanism_draft_response(
        validated_response,
        readiness_report,
    )
    if validation.get("blocked") or not validation.get("is_valid"):
        return None
    payload = readiness_report.get("mechanism_prompt_payload_preview") or {}
    source_notes = deepcopy(list(payload.get("source_inspiration_notes") or []))
    return {
        "draft_status": "pending",
        "review_status": "pending",
        "source": "pasted_chatgpt_json",
        "draft_json": deepcopy(dict(validated_response)),
        "source_inspiration_note_ids": _source_note_ids(payload),
        "evidence_chunk_ids": list(validated_response.get("evidence_chunk_ids") or []),
        "immutable_inspiration_provenance": source_notes,
        "mechanism_card_created": False,
        "persistence_status": "preview_only",
    }


def _explicit_group_readiness_context(
    readiness_reports: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    blockers = []
    for report in readiness_reports:
        result = mechanism_draft_prompt_service.build_mechanism_draft_prompt(
            report,
            include_prompt_text=False,
            include_expected_schema=False,
        )
        if result["blocked"]:
            blockers.append(
                f"{report.get('client_note_id', 'unknown')}:{result.get('blocked_reason')}"
            )
    if blockers:
        return {}, blockers

    grouped = deepcopy(dict(readiness_reports[0]))
    payloads = [
        report["mechanism_prompt_payload_preview"] for report in readiness_reports
    ]
    note_ids = list(
        dict.fromkeys(
            note_id for payload in payloads for note_id in _source_note_ids(payload)
        )
    )
    grouped_payload = deepcopy(dict(payloads[0]))
    grouped_payload.update(
        {
            "user_note_text": None,
            "selected_text": None,
            "source_inspiration_note_id": None,
            "source_inspiration_note_ids": note_ids,
            "grouping": {
                "mode": "explicit_note_group",
                "user_selected_merge": True,
                "bound_inspiration_note_ids": note_ids,
            },
            "source_inspiration_notes": _merge_unique(
                payloads, "source_inspiration_notes", "inspiration_note_id"
            ),
            "evidence": _merge_unique(payloads, "evidence", "chunk_id"),
            "approved_objects": _merge_unique(payloads, "approved_objects", "object_id"),
            "candidate_objects": _merge_unique(payloads, "candidate_objects", "object_id"),
            "evidence_chunk_ids": list(
                dict.fromkeys(
                    chunk_id
                    for payload in payloads
                    for chunk_id in payload.get("evidence_chunk_ids") or []
                )
            ),
        }
    )
    grouped["client_note_id"] = "explicit-group:" + ",".join(str(item) for item in note_ids)
    grouped["server_note_id"] = None
    grouped["mechanism_prompt_payload_preview"] = grouped_payload
    return grouped, []


def _merge_unique(
    payloads: list[Mapping[str, Any]],
    field: str,
    identity_field: str,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in payload.get(field) or []:
            if not isinstance(item, Mapping):
                continue
            identity = str(item.get(identity_field) or item)
            if identity in seen:
                continue
            seen.add(identity)
            values.append(deepcopy(dict(item)))
    return values


def _source_note_ids(payload: Mapping[str, Any]) -> list[Any]:
    grouped = payload.get("source_inspiration_note_ids")
    if isinstance(grouped, list) and grouped:
        return list(grouped)
    single = payload.get("source_inspiration_note_id")
    if single is not None:
        return [single]
    return [
        item.get("inspiration_note_id")
        for item in payload.get("source_inspiration_notes") or []
        if isinstance(item, Mapping) and item.get("inspiration_note_id") is not None
    ]


def _evidence_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    note_ids = _source_note_ids(payload)
    return {
        "source_inspiration_note_id": note_ids[0] if len(note_ids) == 1 else None,
        "source_inspiration_note_ids": note_ids,
        "evidence_chunk_ids": list(payload.get("evidence_chunk_ids") or []),
        "approved_objects": deepcopy(list(payload.get("approved_objects") or [])),
        "candidate_objects": deepcopy(list(payload.get("candidate_objects") or [])),
        "pdf_page": payload.get("pdf_page"),
    }


def _prompt_payload_from_source_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    primary_note = pack.get("primary_user_note") if isinstance(pack.get("primary_user_note"), Mapping) else {}
    primary_excerpt = (
        pack.get("primary_source_excerpt")
        if isinstance(pack.get("primary_source_excerpt"), Mapping)
        else {}
    )
    context_sources = (
        pack.get("context_sources") if isinstance(pack.get("context_sources"), Mapping) else {}
    )
    linked_knowledge = (
        pack.get("linked_knowledge") if isinstance(pack.get("linked_knowledge"), Mapping) else {}
    )
    matched_chunks = _mapping_list(context_sources.get("matched_chunks"))
    nearby_chunks = _mapping_list(context_sources.get("nearby_chunks"))
    approved_objects = _mapping_list(linked_knowledge.get("objects"))
    source_note_id = primary_note.get("note_id") or primary_note.get("server_note_id") or primary_note.get("client_note_id")
    evidence_chunk_ids = _unique_ints(
        [
            primary_excerpt.get("chunk_id"),
            *[chunk.get("chunk_id") for chunk in matched_chunks],
        ]
    )
    source_object_ids = _unique_ints(
        [
            item.get("object_id") or item.get("id")
            for item in approved_objects
        ]
    )
    payload = {
        "schema_version": "mechanism_prompt_input_v1",
        "source_mode": pack.get("source_mode") or "unknown",
        "source_inspiration_note_id": source_note_id,
        "source_inspiration_note_ids": [source_note_id] if source_note_id is not None else [],
        "source_inspiration_notes": [
            {
                "inspiration_note_id": source_note_id,
                "user_note_text": primary_note.get("note_text") or "",
                "selected_text": primary_excerpt.get("selected_text") or "",
                "tags": list(primary_note.get("tags") or []),
            }
        ],
        "user_note_text": primary_note.get("note_text") or "",
        "selected_text": primary_excerpt.get("selected_text") or "",
        "chunk_text": primary_excerpt.get("chunk_text") or "",
        "pdf_page": primary_excerpt.get("pdf_page"),
        "page_label": primary_excerpt.get("page_label"),
        "source_chunk_ids": evidence_chunk_ids,
        "source_object_ids": source_object_ids,
        "evidence_chunk_ids": evidence_chunk_ids,
        "evidence": matched_chunks,
        "nearby_context": _nearby_context_text(nearby_chunks),
        "approved_objects": approved_objects,
        "candidate_objects": [],
        "mechanism_source_pack": deepcopy(dict(pack)),
        "primary_user_note": deepcopy(dict(primary_note)),
        "primary_source_excerpt": deepcopy(dict(primary_excerpt)),
        "source_balance_policy": deepcopy(dict(pack.get("source_balance_policy") or {})),
        "source_coverage_requirements": list(pack.get("source_coverage_requirements") or []),
        "citation_tokens": list(pack.get("citation_tokens") or []),
    }
    return payload


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _nearby_context_text(chunks: list[Mapping[str, Any]]) -> str:
    return "\n".join(
        str(chunk.get("chunk_text") or chunk.get("nearby_context") or "").strip()
        for chunk in chunks
        if str(chunk.get("chunk_text") or chunk.get("nearby_context") or "").strip()
    )


def _unique_ints(values: list[Any]) -> list[int]:
    results: list[int] = []
    for value in values:
        try:
            integer = int(value)
        except (TypeError, ValueError):
            continue
        if integer not in results:
            results.append(integer)
    return results


def _manual_workflow_header() -> str:
    return (
        "MANUAL CHATGPT COPY/PASTE WORKFLOW\n"
        "Copy this complete prompt into ChatGPT. Paste back JSON only into NOTEBOOK_AI for validation.\n"
        "NOTEBOOK_AI does not call a model and will not automatically accept or persist the draft.\n\n"
    )


def _export_package(
    *,
    status: str,
    binding_mode: str,
    metadata: dict[str, Any],
    blockers: list[str] | None = None,
    copy_ready_prompt: str | None = None,
    prompt_payload_json: dict[str, Any] | None = None,
    expected_response_schema: dict[str, Any] | None = None,
    evidence_summary: dict[str, Any] | None = None,
    paste_back_readiness_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "export_mode": EXPORT_MODE,
        "binding_mode": binding_mode,
        "copy_ready_prompt": copy_ready_prompt,
        "prompt_payload_json": prompt_payload_json,
        "expected_response_schema": expected_response_schema,
        "evidence_summary": evidence_summary,
        "prompt_export_metadata": metadata,
        "paste_back_readiness_context": paste_back_readiness_context,
        "instructions_for_user": list(USER_INSTRUCTIONS),
        "blockers": list(blockers or []),
        **_safety_flags(),
    }


def _batch_result(
    packages: list[dict[str, Any]],
    *,
    batch_mode: str,
    chapter_id: int | None,
    import_batch_id: str | None,
) -> dict[str, Any]:
    blocked_count = sum(item["status"] != "OK" for item in packages)
    if not packages or blocked_count == len(packages):
        status = "BLOCKED"
    elif blocked_count:
        status = "PARTIAL"
    else:
        status = "OK"
    return {
        "status": status,
        "export_mode": EXPORT_MODE,
        "batch_mode": batch_mode,
        "prompt_packages": packages,
        "prompt_count": len(packages) - blocked_count,
        "blocked_count": blocked_count,
        "chapter_id": chapter_id,
        "import_batch_id": import_batch_id,
        "blockers": [
            blocker for package in packages for blocker in package.get("blockers") or []
        ],
        **_safety_flags(),
    }


def _safety_flags() -> dict[str, bool]:
    return {
        "llm_called": False,
        "external_model_called": False,
        "db_write_performed": False,
        "mechanism_generated": False,
        "mechanism_draft_persisted": False,
        "mechanism_card_created": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
    }

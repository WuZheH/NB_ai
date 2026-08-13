from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

from app.runtime.machine_config import load_runtime_machine_config
from app.runtime.model_readiness import public_model_readiness

from app.api.schemas import (
    PatchPreflightRequest,
    ResearchSessionDryRunRequest,
    ReviewDecisionApplyDryRunRequest,
    ReviewQueueBuildDryRunRequest,
    SandboxRehearsalRequest,
    safety_fields,
)
from app.services.human_review_decision_application import apply_human_review_decisions
from app.services.human_review_patch_preflight import build_persistence_patch_preflight
from app.services.human_review_persistence_patch_builder import build_human_review_persistence_patch_plan
from app.services.human_review_queue_builder import build_human_review_queue
from app.services import vector_store_worker


router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    machine_config = load_runtime_machine_config()
    readiness = public_model_readiness()
    return {
        "status": "ok",
        "app": "Search",
        "mode": "local_first",
        **readiness,
        "machine_config": machine_config.public_status(),
        **vector_store_worker.vector_auto_sync_boundary(),
        **safety_fields(),
    }


@router.get("/api/v1/system/boundary")
def system_boundary() -> dict[str, Any]:
    return {
        "status": "ok",
        "local_first": True,
        "production_db_write_enabled": False,
        "llm_external_api_enabled": False,
        "autonomous_agent_enabled": False,
        "final_hypothesis_enabled": False,
        "accepted_tag_auto_write_enabled": False,
        "confirmed_relation_auto_write_enabled": False,
        **vector_store_worker.vector_auto_sync_boundary(),
        **safety_fields(),
    }


@router.post("/api/v1/research/session/dry-run")
def research_session_dry_run(request: ResearchSessionDryRunRequest) -> dict[str, Any]:
    return {
        "status": "not_connected_yet",
        "research_goal": request.research_goal,
        "top_k": request.top_k,
        "message": "Research session dry-run endpoint shell created; service wiring deferred",
        **safety_fields(),
    }


@router.post("/api/v1/review/queue/build-dry-run")
def review_queue_build_dry_run(request: ReviewQueueBuildDryRunRequest) -> dict[str, Any]:
    try:
        queue = build_human_review_queue(request.research_session_output)
    except Exception as exc:
        return {
            "status": "validation_failed",
            "validation_errors": [{"error_message": str(exc)}],
            **safety_fields(),
        }
    return {
        "status": "connected",
        "review_queue": queue,
        **safety_fields(),
    }


@router.post("/api/v1/review/decision/apply-dry-run")
def review_decision_apply_dry_run(request: ReviewDecisionApplyDryRunRequest) -> dict[str, Any]:
    decision_result = apply_human_review_decisions(request.review_queue, request.decisions)
    patch_plan = (
        build_human_review_persistence_patch_plan(decision_result)
        if decision_result.get("ok") is True
        else {
            "ok": False,
            "patch_entries": [],
            "validation_errors": decision_result.get("validation_errors", []),
            "summary": {"patch_entry_count": 0},
            "safety_flags": {"dry_run": True, "production_db_written": False},
        }
    )
    return {
        "status": "connected" if decision_result.get("ok") else "validation_failed",
        "decision_result": decision_result,
        "patch_plan": patch_plan,
        **safety_fields(),
    }


@router.post("/api/v1/review/patch/preflight")
def review_patch_preflight(request: PatchPreflightRequest) -> dict[str, Any]:
    preflight = build_persistence_patch_preflight(request.patch_plan)
    return {
        "status": "connected",
        "preflight": preflight,
        **safety_fields(),
    }


@router.post("/api/v1/review/sandbox/rehearsal")
def sandbox_rehearsal(request: SandboxRehearsalRequest) -> dict[str, Any]:
    return {
        "status": "not_connected_yet",
        "message": "Sandbox rehearsal endpoint shell created; in-memory sandbox DB wiring deferred in Phase 16A",
        "patch_plan_received": bool(request.patch_plan),
        "preflight_package_received": bool(request.preflight_package),
        "sandbox_only": True,
        **safety_fields(),
    }


@router.get("/api/v1/product/routes")
def product_routes(request: Request) -> dict[str, Any]:
    routes = _registered_routes(request)
    return {
        "status": "ok",
        "routes": routes,
        **safety_fields(),
    }


def _registered_routes(request: Request) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for route in request.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            path = route.path
            meta = _route_meta(method, path)
            items.append(
                {
                    "method": method,
                    "path": path,
                    "implementation_status": "connected",
                    **meta,
                }
            )
    items.extend(_manual_flow_registry_items())
    return sorted(items, key=lambda item: (item["path"], item["method"]))


def _manual_flow_registry_items() -> list[dict[str, Any]]:
    return [
        {
            "method": "MANUAL",
            "path": "/manual/note-to-mechanism-review-gates",
            "implementation_status": "connected",
            "category": "review_flow",
            "write_capability": False,
            "requires_confirmation": False,
            "llm_called": False,
            "vector_write": False,
            "zotero_db_write": False,
            "status": "manual_flow",
            "review_gates": [
                "note_correction_review",
                "note_classification_review",
                "object_review",
                "mechanism_review",
            ],
            "mechanism_review_layers": [
                "evidence_review",
                "abstraction_review",
                "classification_review",
                "relationship_review",
                "search_entry_review",
            ],
        }
    ]


def _route_meta(method: str, path: str) -> dict[str, Any]:
    write_capability = method in {"POST", "PUT", "PATCH", "DELETE"}
    if path.endswith("/note-correction-review/save-canary-plan"):
        write_capability = False
    requires_confirmation = (
        "/commit" in path
        or path.endswith("/objects/commit")
        or path.endswith("/zotero-notes/apply")
        or path.endswith("/note-correction-review/save")
        or path.endswith("/persist-candidate-tempdb")
    )
    status = "active"
    category = "product"
    if path.startswith("/api/v1/imports"):
        category = "import"
    elif path.startswith("/api/v1/library/import"):
        category = "pdf_import"
    elif path.startswith("/api/v1/library/books"):
        category = "book_chapter"
    elif path.startswith("/api/v1/library/vector-store"):
        category = "vector_store"
    elif path.startswith("/api/v1/library"):
        category = "library"
    elif path.startswith("/api/v1/zotero"):
        category = "zotero"
    elif path.startswith("/api/v1/review") or path.startswith("/api/v1/research"):
        category = "review_flow"

    if "upsert" in path or "batch-upsert" in path or path.endswith("/object-bundle"):
        status = "legacy"
    elif "dry-run" in path or "preview" in path or "status" in path or "canary-plan" in path:
        status = "dry_run"
    elif "chatgpt-object-tag-input" in path or "mechanism" in path:
        status = "manual_flow"

    return {
        "category": category,
        "write_capability": write_capability,
        "requires_confirmation": requires_confirmation,
        "llm_called": False,
        "vector_write": False,
        "zotero_db_write": False,
        "status": status,
    }

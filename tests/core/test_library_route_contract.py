from __future__ import annotations

import hashlib
import json

from fastapi.routing import APIRoute

from app.api import library_api


EXPECTED_LIBRARY_ROUTE_COUNT = 36
EXPECTED_LIBRARY_ROUTE_FINGERPRINT = (
    "c967435b38d26fba0e4946a12d30981ffd53453e5234f8f0533dbfc73a636d70"
)


def _route_contract() -> list[dict[str, object]]:
    contract: list[dict[str, object]] = []
    for index, route in enumerate(library_api.router.routes):
        if not isinstance(route, APIRoute):
            continue
        contract.append(
            {
                "index": index,
                "path": route.path,
                "methods": sorted(route.methods or ()),
                "name": route.name,
                "operation_id": route.operation_id,
                "unique_id": route.unique_id,
                "response_model": repr(route.response_model),
                "status_code": route.status_code,
                "tags": list(route.tags or ()),
                "dependencies": [repr(item) for item in route.dependencies or ()],
                "endpoint_name": route.endpoint.__name__,
            }
        )
    return contract


def test_library_route_metadata_and_order_match_the_canonical_product_contract() -> None:
    contract = _route_contract()
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(contract) == EXPECTED_LIBRARY_ROUTE_COUNT
    assert hashlib.sha256(encoded).hexdigest() == EXPECTED_LIBRARY_ROUTE_FINGERPRINT


def test_library_facade_endpoints_are_the_registered_endpoint_objects() -> None:
    for route in library_api.router.routes:
        if isinstance(route, APIRoute):
            assert route.endpoint is getattr(library_api, route.name)


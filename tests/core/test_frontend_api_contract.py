from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "frontend" / "src"
SHARED_CLIENT = SOURCE_ROOT / "shared" / "api" / "client.js"
LEGACY_CLIENT = SOURCE_ROOT / "api" / "client.js"


def test_json_client_preserves_base_url_and_public_surface() -> None:
    source = SHARED_CLIENT.read_text(encoding="utf-8")
    assert 'const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";' in source
    assert "import.meta.env.VITE_API_BASE_URL" in source
    assert "VITE_API_BASE_URL || DEFAULT_API_BASE_URL" in source
    for export_name in ("requestJson", "getJson", "postJson"):
        assert f"export " in source and f"function {export_name}" in source
    assert "fetch(`${API_BASE_URL}${path}`" in source
    assert 'Accept: "application/json"' in source
    assert '"Content-Type": "application/json"' in source
    assert "JSON.stringify(body)" in source
    assert "response.text()" in source
    assert "isJsonContentType" in source
    assert "parseJsonPayload" in source


def test_json_client_preserves_error_abort_and_timeout_contracts() -> None:
    source = SHARED_CLIENT.read_text(encoding="utf-8")
    assert "class ApiRequestError" in source
    assert "this.status = status" in source
    assert "this.payload = payload" in source
    assert "this.backendCode = backendCode" in source
    for error_code in (
        "api_connection_failed",
        "api_request_timeout",
        "api_endpoint_not_found",
        "api_internal_error",
        "api_response_content_type_invalid",
        "api_response_json_invalid",
    ):
        assert error_code in source
    assert "new AbortController()" in source
    assert 'signal.addEventListener("abort"' in source
    assert "const timer = setTimeout" in source
    assert "controller.abort" in source
    assert "clearTimeout(timer)" in source


def test_legacy_client_is_a_thin_compatibility_facade() -> None:
    assert LEGACY_CLIENT.read_text(encoding="utf-8").strip() == 'export * from "../shared/api/client.js";'


def test_native_fetch_is_limited_to_json_client_and_binary_pdf_reader() -> None:
    fetch_users = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*")
        if path.suffix in {".js", ".jsx"} and "fetch(" in path.read_text(encoding="utf-8")
    }
    assert fetch_users == {"PdfCoverThumbnail.jsx", "shared/api/client.js"}


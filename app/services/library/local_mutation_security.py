from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

from fastapi import HTTPException, Request


LOCAL_RENDERER_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
LOCAL_API_HOSTS = {
    "127.0.0.1:8000",
    "localhost:8000",
    "[::1]:8000",
}
FORWARDED_HEADERS = {
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "cf-connecting-ip",
    "cf-ray",
}
MAX_MUTATION_BODY_BYTES = 64 * 1024
SESSION_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class MutationSession:
    digest: str
    expires_at: float
    client_host: str
    origin: str


_LOCK = threading.RLock()
_SESSIONS: dict[str, MutationSession] = {}
_RATE_WINDOWS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def issue_mutation_session(request: Request) -> dict[str, Any]:
    context = require_local_renderer(request, rate_scope="mutation_session", rate_limit=10)
    token = secrets.token_urlsafe(32)
    digest = _digest(token)
    expires_at = time.monotonic() + SESSION_TTL_SECONDS
    with _LOCK:
        _purge_sessions(time.monotonic())
        _SESSIONS[digest] = MutationSession(
            digest=digest,
            expires_at=expires_at,
            client_host=context["client_host"],
            origin=context["origin"],
        )
    return {
        "status": "ok",
        "mutation_token": token,
        "expires_in_seconds": SESSION_TTL_SECONDS,
        "scope": "local_desktop_library_mutations",
    }


def require_local_renderer(
    request: Request,
    *,
    rate_scope: str,
    rate_limit: int = 60,
) -> dict[str, str]:
    _enforce_body_limit(request)
    forwarded = sorted(name for name in FORWARDED_HEADERS if request.headers.get(name))
    if forwarded:
        _deny("library_mutation_forwarded_request_forbidden", 403)

    client_host = str(request.client.host if request.client else "")
    if not _is_loopback(client_host):
        _deny("library_mutation_loopback_required", 403)
    origin = str(request.headers.get("origin") or "").rstrip("/")
    if origin not in LOCAL_RENDERER_ORIGINS:
        _deny("library_mutation_renderer_origin_required", 403)
    host = str(request.headers.get("host") or "").lower()
    if host not in LOCAL_API_HOSTS:
        _deny("library_mutation_local_host_required", 403)
    _enforce_rate_limit(client_host, rate_scope, limit=rate_limit)
    return {"client_host": client_host, "origin": origin}


def require_mutation_token(
    request: Request,
    *,
    rate_scope: str,
    rate_limit: int = 20,
) -> dict[str, str]:
    context = require_local_renderer(
        request,
        rate_scope=rate_scope,
        rate_limit=rate_limit,
    )
    token = str(request.headers.get("x-search-mutation-token") or "")
    if len(token) < 32 or len(token) > 256:
        _deny("library_mutation_token_required", 403)
    digest = _digest(token)
    now = time.monotonic()
    with _LOCK:
        _purge_sessions(now)
        session = _SESSIONS.get(digest)
    if session is None or session.expires_at <= now:
        _deny("library_mutation_token_invalid_or_expired", 403)
    if session.client_host != context["client_host"] or session.origin != context["origin"]:
        _deny("library_mutation_token_context_mismatch", 403)
    return context


def reset_security_state_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
        _RATE_WINDOWS.clear()


def _enforce_body_limit(request: Request) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        size = int(raw)
    except ValueError:
        _deny("library_mutation_content_length_invalid", 400)
    if size < 0 or size > MAX_MUTATION_BODY_BYTES:
        _deny("library_mutation_request_too_large", 413)


def _enforce_rate_limit(client_host: str, scope: str, *, limit: int) -> None:
    now = time.monotonic()
    cutoff = now - 60.0
    key = (client_host, scope)
    with _LOCK:
        window = _RATE_WINDOWS[key]
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            _deny("library_mutation_rate_limited", 429)
        window.append(now)


def _purge_sessions(now: float) -> None:
    expired = [digest for digest, session in _SESSIONS.items() if session.expires_at <= now]
    for digest in expired:
        _SESSIONS.pop(digest, None)


def _is_loopback(value: str) -> bool:
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _deny(error_code: str, status_code: int) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "status": "error",
            "error_code": error_code,
            "message": "该书架管理请求未通过本地 Desktop 安全校验。",
        },
    )

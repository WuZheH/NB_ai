from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health"
FRONTEND_URL = "http://127.0.0.1:5173"
NOTE_CORRECTION_PACKAGE_URL = (
    "http://127.0.0.1:8000/api/v1/library/books/10/chapters/69/note-correction-package"
)
RESTART_HINT = (
    "当前后端进程可能未加载最新代码，请关闭 backend/frontend 窗口后重新运行 "
    "scripts/start_notebook_ai_dev.bat。"
)


@dataclass(frozen=True)
class HttpCheck:
    ok: bool
    status: int | None
    detail: str
    payload: Any = None


def fetch_json(url: str, *, timeout: float = 5.0) -> HttpCheck:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return HttpCheck(True, response.status, "ok", json.loads(raw))
    except HTTPError as exc:
        return HttpCheck(False, exc.code, f"HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return HttpCheck(False, None, str(exc))


def fetch_frontend(url: str, *, timeout: float = 5.0) -> HttpCheck:
    request = Request(url, headers={"Accept": "text/html"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(1200).decode("utf-8", errors="replace")
            ok = response.status == 200 and ("<html" in body.lower() or "<!doctype html" in body.lower())
            detail = "ok" if ok else "front page did not look like Vite HTML"
            return HttpCheck(ok, response.status, detail)
    except HTTPError as exc:
        return HttpCheck(False, exc.code, f"HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError) as exc:
        return HttpCheck(False, None, str(exc))


def analyze_note_correction_package(payload: dict[str, Any]) -> dict[str, Any]:
    package = _parse_package_json(payload.get("package_json"))
    chapter_context = package.get("chapter_context") if isinstance(package, dict) else None
    note_anchors = package.get("note_anchors") if isinstance(package, dict) else None
    interleaved_view = package.get("interleaved_markdown_view") if isinstance(package, dict) else None
    candidates = package.get("correction_candidates") if isinstance(package, dict) else None
    supporting_evidence = package.get("supporting_evidence") if isinstance(package, dict) else None

    chapter_markdown = ""
    if isinstance(chapter_context, dict):
        chapter_markdown = str(
            chapter_context.get("chapter_markdown")
            or chapter_context.get("chapter_md_text")
            or ""
        )

    pn68 = _find_by_annotation_key(candidates, "SYNPN068")
    pn68_warnings = pn68.get("warnings") if isinstance(pn68, dict) else []
    if not isinstance(pn68_warnings, list):
        pn68_warnings = [str(pn68_warnings)]
    pn68_warning_text = " ".join(str(item) for item in pn68_warnings)
    pn68_is_unmatched = bool(
        isinstance(pn68, dict)
        and (
            pn68.get("anchor_method") == "unmatched"
            or pn68.get("matched_chunk_id") is None
            or "unmatched_user_note" in pn68_warning_text
            or "alignment_uncertain" in pn68_warning_text
        )
    )

    checks = {
        "chapter_context": isinstance(chapter_context, dict),
        "chapter_markdown_or_md_text": bool(chapter_markdown.strip()),
        "note_anchors": isinstance(note_anchors, list) and len(note_anchors) > 0,
        "interleaved_markdown_view": isinstance(interleaved_view, str) and bool(interleaved_view.strip()),
        "correction_candidates_67": isinstance(candidates, list) and len(candidates) == 67,
        "supporting_evidence_1": isinstance(supporting_evidence, list) and len(supporting_evidence) == 1,
        "pn68yptt_unmatched_warning": pn68_is_unmatched,
    }

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "counts": {
            "chapter_markdown_chars": len(chapter_markdown),
            "note_anchors": len(note_anchors) if isinstance(note_anchors, list) else 0,
            "correction_candidates": len(candidates) if isinstance(candidates, list) else 0,
            "supporting_evidence": len(supporting_evidence) if isinstance(supporting_evidence, list) else 0,
            "interleaved_markdown_view_chars": len(interleaved_view) if isinstance(interleaved_view, str) else 0,
        },
        "pn68yptt": {
            "present": isinstance(pn68, dict),
            "anchor_method": pn68.get("anchor_method") if isinstance(pn68, dict) else None,
            "warnings": pn68_warnings,
        },
    }


def run_checks(*, timeout: float = 5.0) -> dict[str, Any]:
    health = fetch_json(BACKEND_HEALTH_URL, timeout=timeout)
    frontend = fetch_frontend(FRONTEND_URL, timeout=timeout)
    package = fetch_json(NOTE_CORRECTION_PACKAGE_URL, timeout=timeout)
    package_analysis = (
        analyze_note_correction_package(package.payload)
        if package.ok and isinstance(package.payload, dict)
        else None
    )
    ok = health.ok and frontend.ok and package.ok and bool(package_analysis and package_analysis["ok"])
    return {
        "ok": ok,
        "health": health,
        "frontend": frontend,
        "package": package,
        "package_analysis": package_analysis,
    }


def print_report(result: dict[str, Any]) -> None:
    print("NOTEBOOK_AI dev 状态检查（只读）")
    print()
    _print_http_check("后端 health", result["health"])
    _print_http_check("前端页面", result["frontend"])
    _print_http_check("第 8 章 note correction package", result["package"])
    print()

    analysis = result.get("package_analysis")
    if analysis:
        print("最新 package 字段检查：")
        labels = {
            "chapter_context": "chapter_context",
            "chapter_markdown_or_md_text": "chapter_markdown / chapter_md_text",
            "note_anchors": "note_anchors",
            "interleaved_markdown_view": "interleaved_markdown_view",
            "correction_candidates_67": "correction_candidates = 67",
            "supporting_evidence_1": "supporting_evidence = 1",
            "pn68yptt_unmatched_warning": "SYNPN068 unmatched warning",
        }
        for key, label in labels.items():
            mark = "OK" if analysis["checks"].get(key) else "MISSING"
            print(f"  [{mark}] {label}")
        counts = analysis["counts"]
        print(
            "  counts: "
            f"chapter_markdown_chars={counts['chapter_markdown_chars']}, "
            f"note_anchors={counts['note_anchors']}, "
            f"correction_candidates={counts['correction_candidates']}, "
            f"supporting_evidence={counts['supporting_evidence']}, "
            f"interleaved_chars={counts['interleaved_markdown_view_chars']}"
        )
        pn68 = analysis["pn68yptt"]
        print(
            "  SYNPN068: "
            f"present={pn68['present']}, "
            f"anchor_method={pn68['anchor_method']}, "
            f"warnings={pn68['warnings']}"
        )
    else:
        print("最新 package 字段检查：无法读取 package JSON。")

    print()
    if result["ok"]:
        print("结论：当前前后端可访问，后端已加载第 8 章 note correction 最新 package 结构。")
    else:
        print(f"结论：{RESTART_HINT}")
    print()
    print("安全边界：本脚本不写 DB、不写 Zotero、不调用 LLM、不生成对象/关系/机制、不杀进程。")


def _print_http_check(label: str, check: HttpCheck) -> None:
    status = check.status if check.status is not None else "n/a"
    state = "OK" if check.ok else "FAIL"
    print(f"{label}: [{state}] status={status} detail={check.detail}")


def _parse_package_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _find_by_annotation_key(items: Any, key: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("zotero_annotation_key") == key:
            return item
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check NOTEBOOK_AI dev frontend/backend status.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Always exit 0 after printing the report.",
    )
    args = parser.parse_args(argv)

    result = run_checks(timeout=args.timeout)
    print_report(result)
    if args.no_fail:
        return 0
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

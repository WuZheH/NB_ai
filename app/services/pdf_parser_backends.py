from __future__ import annotations

import importlib.util
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.book_import_contract import PdfLayoutBlock, PdfLayoutLine, PdfLayoutSpan
from bs4 import BeautifulSoup

from app.services.pdf_backend_service import load_fitz_backend
from app.services.pdf_conversion_service import postprocess_marker_markdown


DEFAULT_PDF_PARSER_BACKEND = "pymupdf_text"
PYMUPDF_BACKEND = "pymupdf_text"
MARKER_SURYA_BACKEND = "marker_surya"
MARKER_SURYA_PAGE_BLOCKS_BACKEND = "marker_surya_page_blocks"
SUPPORTED_BACKENDS = frozenset(
    {PYMUPDF_BACKEND, MARKER_SURYA_BACKEND, MARKER_SURYA_PAGE_BLOCKS_BACKEND}
)
DEFAULT_MODEL_CACHE_ROOT = Path(r"D:\LEARNING\Tools\model_cache")
DEFAULT_MARKER_MODEL_CACHE = DEFAULT_MODEL_CACHE_ROOT / "datalab" / "models"
LEGACY_MARKER_MODEL_CACHE = Path(r"D:\LEARNING\Tools\marker_cache\datalab\models")


class PdfParserBackendUnavailable(RuntimeError):
    pass


def probe_runtime(*, backend: str | None = None) -> dict[str, Any]:
    """Return runtime environment info for diagnostics.

    Uses a subprocess to safely probe torch/CUDA without importing torch
    in the main FastAPI process. Falls back to find_spec if subprocess fails.
    """
    import json
    import subprocess
    import sys

    torch_spec = importlib.util.find_spec("torch")
    marker_spec = importlib.util.find_spec("marker")
    surya_spec = importlib.util.find_spec("surya")

    info: dict[str, Any] = {
        "python_executable": sys.executable,
        "torch_available": torch_spec is not None,
        "torch_importable": torch_spec is not None,
        "torch_version": None,
        "torch_cuda_version": None,
        "torch_cuda_available": False,
        "cuda_available": False,
        "cuda_device_count": 0,
        "cuda_device_name": None,
        "marker_importable": marker_spec is not None,
        "surya_importable": surya_spec is not None,
        "selected_backend": backend or DEFAULT_PDF_PARSER_BACKEND,
        "expected_device": "unknown",
        "recommended_worker_device": "cpu",
        "reason": "torch_cuda_unavailable",
        "torch_probe_error": None,
        "torch_probe_stderr": None,
    }

    if torch_spec is not None:
        probe_script = (
            "import json, torch; "
            "print(json.dumps({"
            "'torch_version': torch.__version__, "
            "'torch_cuda_version': getattr(torch.version, 'cuda', None), "
            "'cuda_available': torch.cuda.is_available(), "
            "'cuda_device_count': torch.cuda.device_count() if torch.cuda.is_available() else 0, "
            "'cuda_device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() and torch.cuda.device_count() > 0 else None"
            "}))"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", probe_script],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                probe = json.loads(result.stdout.strip())
                info["torch_version"] = probe.get("torch_version")
                info["torch_cuda_version"] = probe.get("torch_cuda_version")
                info["cuda_available"] = probe.get("cuda_available", False)
                info["torch_cuda_available"] = probe.get("cuda_available", False)
                info["cuda_device_count"] = probe.get("cuda_device_count", 0)
                info["cuda_device_name"] = probe.get("cuda_device_name")
                if info["cuda_available"]:
                    info["expected_device"] = "cuda"
                    info["recommended_worker_device"] = "cuda"
                    info["reason"] = "cuda_available"
                else:
                    info["expected_device"] = "cpu"
                    info["recommended_worker_device"] = "cpu"
                    info["reason"] = "torch_cuda_unavailable"
                if result.stderr:
                    info["torch_probe_stderr"] = result.stderr.strip()[:500]
            else:
                info["torch_probe_error"] = f"exit code {result.returncode}: {result.stderr.strip()[:300]}"
                info["expected_device"] = "cpu"
                info["recommended_worker_device"] = "cpu"
                info["reason"] = "probe_failed"
        except subprocess.TimeoutExpired:
            info["torch_probe_error"] = "subprocess timeout (15s)"
            info["expected_device"] = "cpu"
            info["recommended_worker_device"] = "cpu"
            info["reason"] = "probe_failed"
        except Exception as exc:
            info["torch_probe_error"] = str(exc)[:300]
            info["expected_device"] = "cpu"
            info["recommended_worker_device"] = "cpu"
            info["reason"] = "probe_failed"
    else:
        info["expected_device"] = "unknown"
        info["recommended_worker_device"] = "cpu"
        info["reason"] = "torch_cuda_unavailable"

    if (backend or DEFAULT_PDF_PARSER_BACKEND) != PYMUPDF_BACKEND:
        if not info["marker_importable"] or not info["surya_importable"]:
            info["recommended_worker_device"] = "cpu"
            info["reason"] = "marker_cuda_unavailable"
            if info["expected_device"] == "cuda":
                info["expected_device"] = "cpu"

    return info


@dataclass(frozen=True)
class PdfParseResult:
    markdown_text: str
    page_markers_present: bool
    page_count: int
    parser_backend: str
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    block_stats: dict[str, Any] = field(default_factory=dict)
    layout_blocks: list[PdfLayoutBlock] = field(default_factory=list)
    layout_lines: list[PdfLayoutLine] = field(default_factory=list)
    layout_spans: list[PdfLayoutSpan] = field(default_factory=list)
    elapsed_seconds: float | None = None


@dataclass(frozen=True)
class MarkerPageBlockResult:
    pages: list[dict[str, Any]]
    raw_blocks: list[dict[str, Any]]
    page_count: int
    parser_backend: str
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)


def parse_pdf_to_markdown(
    pdf_path: str | Path,
    *,
    backend: str = DEFAULT_PDF_PARSER_BACKEND,
    max_pages: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    marker_model_cache: str | Path | None = None,
    device: str | None = None,
    use_sliced_pdf: bool = False,
    slice_dir: str | Path | None = None,
    cleanup_slice: bool = True,
) -> PdfParseResult:
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported PDF parser backend: {backend}")
    if use_sliced_pdf and (page_start is not None or page_end is not None):
        return parse_pdf_range_via_slice(
            pdf_path,
            backend=backend,
            page_start=page_start or 1,
            page_end=page_end,
            marker_model_cache=marker_model_cache,
            device=device,
            slice_dir=slice_dir,
            cleanup_slice=cleanup_slice,
        )
    if backend == PYMUPDF_BACKEND:
        return parse_pdf_with_pymupdf(
            pdf_path,
            max_pages=max_pages,
            page_start=page_start,
            page_end=page_end,
        )
    if backend == MARKER_SURYA_PAGE_BLOCKS_BACKEND:
        return parse_pdf_with_marker_surya_page_blocks(
            pdf_path,
            max_pages=max_pages,
            page_start=page_start,
            page_end=page_end,
            marker_model_cache=marker_model_cache,
            device=device,
        )
    return parse_pdf_with_marker_surya(
        pdf_path,
        max_pages=max_pages,
        page_start=page_start,
        page_end=page_end,
        marker_model_cache=marker_model_cache,
        device=device,
    )


def check_marker_surya_available(marker_model_cache: str | Path | None = None) -> dict[str, Any]:
    marker_spec = importlib.util.find_spec("marker")
    surya_spec = importlib.util.find_spec("surya")
    pdftext_spec = importlib.util.find_spec("pdftext")
    cache_path, cache_mode = _resolve_marker_model_cache(marker_model_cache)
    required = _marker_required_model_files(cache_path)
    missing = [str(path) for path in required if not path.exists()]
    return {
        "marker_importable": marker_spec is not None,
        "surya_importable": surya_spec is not None,
        "pdftext_importable": pdftext_spec is not None,
        "cache_path": str(cache_path),
        "default_cache_path": str(DEFAULT_MARKER_MODEL_CACHE),
        "legacy_cache_path": str(LEGACY_MARKER_MODEL_CACHE),
        "marker_cache_used": cache_mode == "legacy_read_only_fallback",
        "marker_cache_mode": cache_mode,
        "required_model_files_present": not missing,
        "missing_model_files": missing,
    }


def parse_marker_surya_page_blocks(
    pdf_path: str | Path,
    *,
    max_pages: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    marker_model_cache: str | Path | None = None,
    device: str | None = None,
) -> MarkerPageBlockResult:
    start = time.perf_counter()
    availability = check_marker_surya_available(marker_model_cache)
    if not availability["marker_importable"] or not availability["surya_importable"]:
        raise PdfParserBackendUnavailable("marker or surya is not importable")
    if not availability["required_model_files_present"]:
        raise PdfParserBackendUnavailable(
            "marker/surya local model cache is incomplete: "
            + ", ".join(availability["missing_model_files"])
        )

    cache_path = Path(availability["cache_path"])
    _set_marker_offline_env(cache_path)
    marker_device = _normalize_marker_device(device)
    _set_marker_device_env(marker_device)

    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    page_range, expected_page_numbers = _marker_page_range(
        page_start=page_start,
        page_end=page_end,
        max_pages=max_pages,
    )

    config_parser = ConfigParser(
        {
            "output_format": "chunks",
            "page_range": page_range,
            "disable_multiprocessing": True,
            "disable_image_extraction": True,
            "use_llm": False,
        }
    )
    config = config_parser.generate_config_dict()
    config["disable_tqdm"] = True
    converter = PdfConverter(
        config=config,
        artifact_dict=create_model_dict(device=marker_device),
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=None,
    )
    rendered = converter(str(Path(pdf_path)))
    blocks = [block.model_dump() for block in rendered.blocks]
    page_count = int(converter.page_count or len(rendered.page_info or {}) or len(expected_page_numbers or []))
    pages = group_marker_blocks_by_page(
        blocks,
        page_count=None if expected_page_numbers else page_count,
        page_numbers=expected_page_numbers,
    )
    return MarkerPageBlockResult(
        pages=pages,
        raw_blocks=blocks,
        page_count=page_count,
        parser_backend="marker_surya_page_blocks",
        elapsed_seconds=time.perf_counter() - start,
        warnings=[],
        artifacts={
            "cache_path": str(cache_path),
            "marker_cache_used": availability["marker_cache_used"],
            "marker_cache_mode": availability["marker_cache_mode"],
            "use_llm": False,
            "page_info": rendered.page_info,
            "page_start": page_start,
            "page_end": page_end,
            "page_range": page_range,
            "parser_device": marker_device or "auto",
        },
    )


def group_marker_blocks_by_page(
    blocks: list[dict[str, Any]],
    *,
    page_count: int | None = None,
    page_numbers: list[int] | None = None,
) -> list[dict[str, Any]]:
    block_page_numbers = sorted({_marker_page_number(block) for block in blocks})
    if page_numbers is not None:
        resolved_page_numbers = sorted(set(page_numbers).union(block_page_numbers))
    elif page_count:
        resolved_page_numbers = sorted(set(block_page_numbers).union(range(1, page_count + 1)))
    else:
        resolved_page_numbers = block_page_numbers
    pages: list[dict[str, Any]] = []
    for page_number in resolved_page_numbers:
        page_blocks = [
            block for block in blocks if _marker_page_number(block) == page_number
        ]
        page_blocks.sort(key=lambda block: _block_sort_key(block))
        pages.append({"page_number": page_number, "blocks": page_blocks})
    return pages


def marker_page_blocks_to_markdown(pages: list[dict[str, Any]]) -> str:
    output: list[str] = []
    for page in sorted(pages, key=lambda item: int(item.get("page_number", 0))):
        output.append(f"<!-- PDF_PAGE: {int(page.get('page_number', 0))} -->")
        output.append("")
        for block in page.get("blocks", []):
            text = marker_block_to_markdown(block)
            if text:
                output.append(text)
                output.append("")
    return "\n".join(output).rstrip() + "\n"


def marker_block_to_markdown(block: dict[str, Any]) -> str:
    block_type = str(block.get("block_type") or block.get("type") or "")
    text = _html_to_text(str(block.get("html") or ""))
    if not text:
        text = " ".join(str(block.get("text") or "").split())
    if not text:
        return ""
    if _is_heading_block(block):
        level = _heading_level(block)
        return f"{'#' * level} {text}"
    if block_type.lower() in {"table", "tablegroup"}:
        return _html_table_to_text(str(block.get("html") or "")) or text
    return text


def marker_block_stats(blocks: list[dict[str, Any]], markdown_text: str) -> dict[str, Any]:
    histogram: dict[str, int] = {}
    for block in blocks:
        block_type = str(block.get("block_type") or "unknown")
        histogram[block_type] = histogram.get(block_type, 0) + 1
    summary = summarize_markdown(markdown_text)
    return {
        **summary,
        "block_count": len(blocks),
        "block_type_histogram": histogram,
        "table_block_count": sum(
            count for block_type, count in histogram.items() if "table" in block_type.lower()
        ),
        "figure_block_count": sum(
            count
            for block_type, count in histogram.items()
            if "figure" in block_type.lower() or "picture" in block_type.lower()
        ),
        "caption_block_count": sum(
            count for block_type, count in histogram.items() if "caption" in block_type.lower()
        ),
    }


def parse_pdf_with_pymupdf(
    pdf_path: str | Path,
    *,
    max_pages: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> PdfParseResult:
    start = time.perf_counter()
    pdf = Path(pdf_path)
    warnings: list[str] = []
    lines = [f"<!-- PDF_PATH: {pdf.resolve()} -->", ""]
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    fitz = load_fitz_backend()
    with fitz.open(pdf) as document:
        page_count = len(document)
        start_page, end_page = _normalize_page_range(page_count, page_start, page_end, max_pages)
        for page_number in range(start_page, end_page + 1):
            page_index = page_number - 1
            text = document[page_index].get_text("text") or ""
            lines.append(f"<!-- PDF_PAGE: {page_number} -->")
            lines.append("")
            lines.append(text.strip())
            lines.append("")
    markdown_text = "\n".join(lines).rstrip() + "\n"
    return PdfParseResult(
        markdown_text=markdown_text,
        page_markers_present="<!-- PDF_PAGE:" in markdown_text,
        page_count=page_count,
        parser_backend=PYMUPDF_BACKEND,
        warnings=warnings,
        artifacts={},
        elapsed_seconds=time.perf_counter() - start,
    )


def parse_pdf_with_marker_surya(
    pdf_path: str | Path,
    *,
    max_pages: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    marker_model_cache: str | Path | None = None,
    device: str | None = None,
) -> PdfParseResult:
    start = time.perf_counter()
    availability = check_marker_surya_available(marker_model_cache)
    if not availability["marker_importable"] or not availability["surya_importable"]:
        raise PdfParserBackendUnavailable("marker or surya is not importable")
    if not availability["required_model_files_present"]:
        raise PdfParserBackendUnavailable(
            "marker/surya local model cache is incomplete: "
            + ", ".join(availability["missing_model_files"])
        )

    cache_path = Path(availability["cache_path"])
    _set_marker_offline_env(cache_path)
    marker_device = _normalize_marker_device(device)
    _set_marker_device_env(marker_device)

    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    page_range, expected_page_numbers = _marker_page_range(
        page_start=page_start,
        page_end=page_end,
        max_pages=max_pages,
    )

    config_parser = ConfigParser(
        {
            "output_format": "markdown",
            "page_range": page_range,
            "disable_multiprocessing": True,
            "disable_image_extraction": True,
            "use_llm": False,
        }
    )
    config = config_parser.generate_config_dict()
    config["disable_tqdm"] = True
    artifact_dict = create_model_dict(device=marker_device)
    converter = PdfConverter(
        config=config,
        artifact_dict=artifact_dict,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=None,
    )
    rendered = converter(str(Path(pdf_path)))
    raw_text, output_ext, images = text_from_rendered(rendered)
    markdown_text = postprocess_marker_markdown(raw_text, pdf_path)
    elapsed = time.perf_counter() - start
    return PdfParseResult(
        markdown_text=markdown_text,
        page_markers_present="<!-- PDF_PAGE:" in markdown_text,
        page_count=int(converter.page_count or 0),
        parser_backend=MARKER_SURYA_BACKEND,
        warnings=[],
        artifacts={
            "marker_output_ext": output_ext,
            "image_count": len(images or {}),
            "cache_path": str(cache_path),
            "marker_cache_used": availability["marker_cache_used"],
            "marker_cache_mode": availability["marker_cache_mode"],
            "use_llm": False,
            "page_start": page_start,
            "page_end": page_end,
            "page_range": page_range,
            "expected_page_numbers": expected_page_numbers,
            "parser_device": marker_device or "auto",
        },
        elapsed_seconds=elapsed,
    )


def parse_pdf_with_marker_surya_page_blocks(
    pdf_path: str | Path,
    *,
    max_pages: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    marker_model_cache: str | Path | None = None,
    device: str | None = None,
) -> PdfParseResult:
    result = parse_marker_surya_page_blocks(
        pdf_path,
        max_pages=max_pages,
        page_start=page_start,
        page_end=page_end,
        marker_model_cache=marker_model_cache,
        device=device,
    )
    markdown_text = marker_page_blocks_to_markdown(result.pages)
    stats = marker_block_stats(result.raw_blocks, markdown_text)
    layout_blocks = marker_raw_blocks_to_layout_blocks(
        result.raw_blocks,
        source_backend=MARKER_SURYA_PAGE_BLOCKS_BACKEND,
        page_info=result.artifacts.get("page_info"),
    )
    layout_lines = marker_raw_blocks_to_layout_lines(
        result.raw_blocks,
        source_backend=MARKER_SURYA_PAGE_BLOCKS_BACKEND,
        page_info=result.artifacts.get("page_info"),
    )
    layout_spans = marker_raw_blocks_to_layout_spans(
        result.raw_blocks,
        source_backend=MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    )
    return PdfParseResult(
        markdown_text=markdown_text,
        page_markers_present="<!-- PDF_PAGE:" in markdown_text,
        page_count=result.page_count,
        parser_backend=MARKER_SURYA_PAGE_BLOCKS_BACKEND,
        warnings=list(result.warnings),
        artifacts={
            **result.artifacts,
            "raw_block_count": len(result.raw_blocks),
            "page_count_from_blocks": len(result.pages),
            "source_coordinate_space": "pdf_page",
            "parser_capabilities": {
                "supports_block_bbox": bool(layout_blocks),
                "supports_line_bbox": bool(layout_lines),
                "supports_span_bbox": bool(layout_spans),
            },
        },
        block_stats=stats,
        layout_blocks=layout_blocks,
        layout_lines=layout_lines,
        layout_spans=layout_spans,
        elapsed_seconds=result.elapsed_seconds,
    )


def parse_pdf_range_via_slice(
    pdf_path: str | Path,
    *,
    backend: str,
    page_start: int,
    page_end: int | None,
    marker_model_cache: str | Path | None = None,
    device: str | None = None,
    slice_dir: str | Path | None = None,
    cleanup_slice: bool = True,
) -> PdfParseResult:
    source = Path(pdf_path)
    target_dir = Path(slice_dir or Path(".codex_tmp") / "marker_range_slices")
    target_dir.mkdir(parents=True, exist_ok=True)
    fitz = load_fitz_backend()
    with fitz.open(source) as document:
        total_pages = len(document)
        start_page, end_page = _normalize_page_range(total_pages, page_start, page_end, None)
        slice_path = target_dir / f"{source.stem}_p{start_page}_{end_page}_{os.getpid()}_{int(time.time() * 1000)}.pdf"
        sliced = fitz.open()
        sliced.insert_pdf(document, from_page=start_page - 1, to_page=end_page - 1)
        sliced.save(slice_path)
        sliced.close()
    try:
        result = parse_pdf_to_markdown(
            slice_path,
            backend=backend,
            marker_model_cache=marker_model_cache,
            device=device,
        )
        remapped = remap_pdf_page_markers(result.markdown_text, page_start)
        return PdfParseResult(
            markdown_text=remapped,
            page_markers_present=result.page_markers_present,
            page_count=end_page - start_page + 1,
            parser_backend=result.parser_backend,
            warnings=list(result.warnings),
            artifacts={
                **result.artifacts,
                "sliced_pdf_path": str(slice_path),
                "original_pdf_path": str(source),
                "page_start": start_page,
                "page_end": end_page,
                "slice_fallback_used": True,
            },
            block_stats=summarize_markdown(remapped),
            layout_blocks=_remap_layout_blocks(result.layout_blocks, page_start),
            layout_lines=_remap_layout_lines(result.layout_lines, page_start),
            layout_spans=_remap_layout_spans(result.layout_spans, page_start),
            elapsed_seconds=result.elapsed_seconds,
        )
    finally:
        if cleanup_slice:
            try:
                slice_path.unlink()
            except OSError:
                pass


def remap_pdf_page_markers(markdown_text: str, original_page_start: int) -> str:
    offset = max(0, int(original_page_start) - 1)

    def _replace(match: re.Match[str]) -> str:
        page = int(match.group(1)) + offset
        return f"<!-- PDF_PAGE: {page} -->"

    return re.sub(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->", _replace, markdown_text)


def summarize_markdown(markdown_text: str) -> dict[str, Any]:
    headings = re.findall(r"(?m)^#{1,6}\s+.+$", markdown_text)
    page_markers = re.findall(r"<!--\s*PDF_PAGE:\s*\d+\s*-->", markdown_text)
    lower = markdown_text.lower()
    return {
        "char_count": len(markdown_text),
        "heading_count": len(headings),
        "page_marker_count": len(page_markers),
        "table_markdown_hint_count": markdown_text.count("|"),
        "image_markdown_hint_count": markdown_text.count("!["),
        "formula_hint_count": lower.count("$$") + lower.count("\\(") + lower.count("\\["),
        "first_headings": headings[:10],
    }


def _set_marker_offline_env(cache_path: Path) -> None:
    os.environ["MODEL_CACHE_DIR"] = str(cache_path)
    os.environ["DATALAB_CACHE_DIR"] = str(cache_path.parent.parent)
    os.environ["DATALAB_CACHE"] = str(cache_path.parent.parent)
    os.environ["HF_HOME"] = str(DEFAULT_MODEL_CACHE_ROOT / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(DEFAULT_MODEL_CACHE_ROOT / "huggingface" / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(DEFAULT_MODEL_CACHE_ROOT / "huggingface" / "hub")
    os.environ["TORCH_HOME"] = str(DEFAULT_MODEL_CACHE_ROOT / "torch")
    os.environ["XDG_CACHE_HOME"] = str(DEFAULT_MODEL_CACHE_ROOT)
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("IN_STREAMLIT", "true")


def _normalize_marker_device(device: str | None) -> str | None:
    value = str(device or "").strip().lower()
    if value in {"cuda", "cpu", "mps", "xla"}:
        return value
    return None


def _set_marker_device_env(device: str | None) -> None:
    if device:
        os.environ["TORCH_DEVICE"] = device


def _resolve_marker_model_cache(marker_model_cache: str | Path | None) -> tuple[Path, str]:
    if marker_model_cache is not None:
        path = Path(marker_model_cache)
        mode = "legacy_read_only_explicit" if path == LEGACY_MARKER_MODEL_CACHE else "explicit"
        return path, mode
    if all(path.exists() for path in _marker_required_model_files(DEFAULT_MARKER_MODEL_CACHE)):
        return DEFAULT_MARKER_MODEL_CACHE, "unified_default"
    if all(path.exists() for path in _marker_required_model_files(LEGACY_MARKER_MODEL_CACHE)):
        return LEGACY_MARKER_MODEL_CACHE, "legacy_read_only_fallback"
    return DEFAULT_MARKER_MODEL_CACHE, "unified_default"


def _marker_required_model_files(cache_path: Path) -> list[Path]:
    return [
        cache_path / "layout" / "2025_09_23" / "model.safetensors",
        cache_path / "text_recognition" / "2025_09_23" / "model.safetensors",
        cache_path / "ocr_error_detection" / "2025_02_18" / "model.safetensors",
        cache_path / "table_recognition" / "2025_02_18" / "model.safetensors",
        cache_path / "text_detection" / "2025_05_07" / "model.safetensors",
    ]


def _marker_page_number(block: dict[str, Any]) -> int:
    block_id = str(block.get("id") or "")
    match = re.search(r"/page/(\d+)(?:/|$)", block_id)
    if match:
        return int(match.group(1)) + 1
    page = block.get("page", 0)
    try:
        page_int = int(page)
    except (TypeError, ValueError):
        page_int = 0
    return page_int + 1


def marker_raw_blocks_to_layout_blocks(
    blocks: list[dict[str, Any]],
    *,
    source_backend: str,
    page_info: Any = None,
) -> list[PdfLayoutBlock]:
    layout_blocks: list[PdfLayoutBlock] = []
    for block_index, block in enumerate(blocks):
        text = marker_block_to_markdown(block)
        bbox = _bbox_dict(block.get("bbox"))
        if not text or bbox is None:
            continue
        pdf_page = _marker_page_number(block)
        page_width, page_height = _page_dimensions(page_info, pdf_page)
        normalized = _normalize_layout_text(text)
        layout_blocks.append(
            PdfLayoutBlock(
                pdf_page=pdf_page,
                page_width=page_width,
                page_height=page_height,
                source_backend=source_backend,
                block_index=block_index,
                block_type=str(block.get("block_type") or block.get("type") or "unknown").lower(),
                text=text,
                normalized_text=normalized,
                bbox=bbox,
                polygon=_polygon_points(block.get("polygon")),
                confidence=1.0,
                text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                source_block_id=str(block.get("id") or ""),
                source_coordinate_space="pdf_page",
            )
        )
    return layout_blocks


def marker_raw_blocks_to_layout_lines(
    blocks: list[dict[str, Any]],
    *,
    source_backend: str,
    page_info: Any = None,
) -> list[PdfLayoutLine]:
    layout_lines: list[PdfLayoutLine] = []
    for block_index, block in enumerate(blocks):
        pdf_page = _marker_page_number(block)
        page_width, page_height = _page_dimensions(page_info, pdf_page)
        for line_index, line in enumerate(_nested_layout_items(block, ("line", "lines"))):
            text = _layout_item_text(line)
            bbox = _bbox_dict(line.get("bbox") if isinstance(line, dict) else None)
            if not text or bbox is None:
                continue
            normalized = _normalize_layout_text(text)
            layout_lines.append(
                PdfLayoutLine(
                    pdf_page=pdf_page,
                    page_width=page_width,
                    page_height=page_height,
                    block_index=block_index,
                    line_index=line_index,
                    text=text,
                    normalized_text=normalized,
                    bbox=bbox,
                    source_backend=source_backend,
                    confidence=1.0,
                    text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    source_block_id=str(block.get("id") or ""),
                    source_coordinate_space="pdf_page",
                )
            )
    return layout_lines


def marker_raw_blocks_to_layout_spans(
    blocks: list[dict[str, Any]],
    *,
    source_backend: str,
) -> list[PdfLayoutSpan]:
    layout_spans: list[PdfLayoutSpan] = []
    for block_index, block in enumerate(blocks):
        pdf_page = _marker_page_number(block)
        for span_index, span in enumerate(_nested_layout_items(block, ("span", "spans", "word", "words"))):
            text = _layout_item_text(span)
            bbox = _bbox_dict(span.get("bbox") if isinstance(span, dict) else None)
            if not text or bbox is None:
                continue
            normalized = _normalize_layout_text(text)
            layout_spans.append(
                PdfLayoutSpan(
                    pdf_page=pdf_page,
                    block_index=block_index,
                    line_index=_safe_int(span.get("line_index")) if isinstance(span, dict) else None,
                    span_index=span_index,
                    text=text,
                    normalized_text=normalized,
                    bbox=bbox,
                    source_backend=source_backend,
                    confidence=1.0,
                    text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    source_block_id=str(block.get("id") or ""),
                    source_coordinate_space="pdf_page",
                )
            )
    return layout_spans


def _nested_layout_items(root: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def visit(value: Any, key_hint: str = "") -> None:
        if isinstance(value, dict):
            if key_hint.lower() in names and _bbox_dict(value.get("bbox")) is not None:
                output.append(value)
            for key, child in value.items():
                lower = str(key).lower()
                if lower in names and isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            output.append(item)
                        else:
                            visit(item, lower)
                elif lower in {"children", "blocks", "items", "content", "lines"}:
                    visit(child, lower)
        elif isinstance(value, list):
            for item in value:
                visit(item, key_hint)

    visit(root)
    return output


def _layout_item_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("text", "html", "content"):
        value = item.get(key)
        if value:
            if key == "html":
                return _html_to_text(str(value))
            return " ".join(str(value).split())
    return ""


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _remap_layout_blocks(blocks: list[PdfLayoutBlock], original_page_start: int) -> list[PdfLayoutBlock]:
    if not blocks:
        return []
    offset = max(0, int(original_page_start) - 1)
    return [
        PdfLayoutBlock(
            **{
                **block.to_dict(),
                "pdf_page": int(block.pdf_page) + offset,
            }
        )
        for block in blocks
    ]


def _remap_layout_lines(lines: list[PdfLayoutLine], original_page_start: int) -> list[PdfLayoutLine]:
    if not lines:
        return []
    offset = max(0, int(original_page_start) - 1)
    return [PdfLayoutLine(**{**line.to_dict(), "pdf_page": int(line.pdf_page) + offset}) for line in lines]


def _remap_layout_spans(spans: list[PdfLayoutSpan], original_page_start: int) -> list[PdfLayoutSpan]:
    if not spans:
        return []
    offset = max(0, int(original_page_start) - 1)
    return [PdfLayoutSpan(**{**span.to_dict(), "pdf_page": int(span.pdf_page) + offset}) for span in spans]


def _bbox_dict(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        keys = ("x0", "y0", "x1", "y1")
        try:
            bbox = {key: float(value[key]) for key in keys}
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            bbox = {"x0": float(value[0]), "y0": float(value[1]), "x1": float(value[2]), "y1": float(value[3])}
        except (TypeError, ValueError):
            return None
    else:
        return None
    if bbox["x1"] <= bbox["x0"] or bbox["y1"] <= bbox["y0"]:
        return None
    return bbox


def _polygon_points(value: Any) -> list[dict[str, float]] | None:
    if not isinstance(value, list):
        return None
    points: list[dict[str, float]] = []
    for point in value:
        if isinstance(point, dict):
            try:
                points.append({"x": float(point["x"]), "y": float(point["y"])})
            except (KeyError, TypeError, ValueError):
                continue
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append({"x": float(point[0]), "y": float(point[1])})
            except (TypeError, ValueError):
                continue
    return points or None


def _page_dimensions(page_info: Any, pdf_page: int) -> tuple[float | None, float | None]:
    candidates: list[Any] = []
    if isinstance(page_info, dict):
        candidates.extend([page_info.get(pdf_page), page_info.get(str(pdf_page)), page_info.get(pdf_page - 1), page_info.get(str(pdf_page - 1))])
    elif isinstance(page_info, list) and 0 <= pdf_page - 1 < len(page_info):
        candidates.append(page_info[pdf_page - 1])
    for candidate in candidates:
        if not candidate:
            continue
        width = _first_number(candidate, ("width", "page_width", "w"))
        height = _first_number(candidate, ("height", "page_height", "h"))
        if width and height:
            return width, height
    return None, None


def _first_number(source: Any, keys: tuple[str, ...]) -> float | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        try:
            value = float(source[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _normalize_layout_text(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _block_sort_key(block: dict[str, Any]) -> tuple[float, float, str]:
    bbox = block.get("bbox") or [0, 0, 0, 0]
    if not isinstance(bbox, list) or len(bbox) < 2:
        bbox = [0, 0, 0, 0]
    return (float(bbox[1] or 0), float(bbox[0] or 0), str(block.get("id") or ""))


def _is_heading_block(block: dict[str, Any]) -> bool:
    block_type = str(block.get("block_type") or "").lower()
    if "sectionheader" in block_type or block_type in {"title"}:
        return True
    html = str(block.get("html") or "").lower()
    return bool(re.search(r"<h[1-6][\s>]", html))


def _heading_level(block: dict[str, Any]) -> int:
    html = str(block.get("html") or "").lower()
    match = re.search(r"<h([1-6])[\s>]", html)
    if match:
        return int(match.group(1))
    hierarchy = block.get("section_hierarchy") or {}
    if isinstance(hierarchy, dict) and hierarchy:
        try:
            return max(1, min(6, max(int(key) for key in hierarchy.keys())))
        except (TypeError, ValueError):
            return 2
    return 2


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(" ").split())


def _html_table_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(cell.get_text(" ").split()) for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in normalized]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def _marker_page_range(
    *,
    page_start: int | None,
    page_end: int | None,
    max_pages: int | None,
) -> tuple[str | None, list[int] | None]:
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    if page_start is None and page_end is None:
        if max_pages is None:
            return None, None
        return f"0-{max_pages - 1}", list(range(1, max_pages + 1))
    start = int(page_start or 1)
    if start < 1:
        raise ValueError("page_start must be >= 1")
    end = int(page_end) if page_end is not None else start
    if max_pages is not None:
        end = min(end, start + max_pages - 1)
    if end < start:
        raise ValueError("page_end must be >= page_start")
    return f"{start - 1}-{end - 1}", list(range(start, end + 1))


def _normalize_page_range(
    page_count: int,
    page_start: int | None,
    page_end: int | None,
    max_pages: int | None,
) -> tuple[int, int]:
    if page_count < 1:
        raise ValueError("PDF has no pages")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    start = int(page_start or 1)
    if start < 1:
        raise ValueError("page_start must be >= 1")
    if start > page_count:
        raise ValueError(f"page_start exceeds PDF page count: {start} > {page_count}")
    end = int(page_end) if page_end is not None else page_count
    if max_pages is not None:
        end = min(end, start + max_pages - 1)
    end = min(end, page_count)
    if end < start:
        raise ValueError("page_end must be >= page_start")
    return start, end


def _page_limit(page_count: int, max_pages: int | None) -> int:
    if max_pages is None:
        return page_count
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    return min(page_count, max_pages)

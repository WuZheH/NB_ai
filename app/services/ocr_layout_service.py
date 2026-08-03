from __future__ import annotations

import importlib.util
import os
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.paths import MODEL_CACHE_ROOT
from app.services.book_import_contract import PdfLayoutLine, PdfLayoutSpan
from app.services.pdf_layout_service import layout_text_hash, normalize_layout_text


DEFAULT_MODEL_CACHE_ROOT = MODEL_CACHE_ROOT


@dataclass(frozen=True)
class OcrPageLayout:
    pdf_page: int
    page_width: float
    page_height: float
    image_width: int
    image_height: int
    page_text_layer_length: int
    lines: list[PdfLayoutLine]
    spans: list[PdfLayoutSpan]
    coordinate_space: str = "pdf_points"
    ocr_backend: str = "surya"
    model_cache_root: str = str(DEFAULT_MODEL_CACHE_ROOT)
    gpu_used: bool = False
    extracted_text: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    traceback_tail: str | None = None


def configure_model_cache_environment(model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT) -> dict[str, str]:
    root = Path(model_cache_root).resolve()
    datalab_root = root / "datalab"
    datalab_models = datalab_root / "models"
    hf_home = root / "huggingface"
    hf_hub = hf_home / "hub"
    values = {
        "MODEL_CACHE_ROOT": str(root),
        "HF_HOME": str(hf_home),
        "HF_HUB_CACHE": str(hf_hub),
        "TRANSFORMERS_CACHE": str(hf_hub),
        "XDG_CACHE_HOME": str(root),
        "TORCH_HOME": str(root / "torch"),
        "DATALAB_CACHE_DIR": str(datalab_root),
        "DATALAB_CACHE": str(datalab_root),
        "MODEL_CACHE_DIR": str(datalab_models),
    }
    for key, value in values.items():
        os.environ[key] = value
    return values


def inspect_ocr_model_cache(
    *,
    model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT,
) -> dict[str, Any]:
    values = configure_model_cache_environment(model_cache_root)
    surya_model_cache_dir = values["MODEL_CACHE_DIR"]
    required_paths: dict[str, str] = {}
    if _module_available("surya"):
        try:
            from surya.settings import settings

            try:
                settings.MODEL_CACHE_DIR = values["MODEL_CACHE_DIR"]
            except Exception:
                pass
            surya_model_cache_dir = str(Path(settings.MODEL_CACHE_DIR))
            checkpoints = {
                "foundation": settings.FOUNDATION_MODEL_CHECKPOINT,
                "recognition": settings.RECOGNITION_MODEL_CHECKPOINT,
                "detection": settings.DETECTOR_MODEL_CHECKPOINT,
            }
            for name, checkpoint in checkpoints.items():
                relative = str(checkpoint).replace("s3://", "").strip("/\\")
                required_paths[name] = str(Path(surya_model_cache_dir) / relative)
        except Exception:
            required_paths = {}
    effective_paths = {
        "model_cache_root": values["MODEL_CACHE_ROOT"],
        "hf_home": values["HF_HOME"],
        "hf_hub_cache": values["HF_HUB_CACHE"],
        "transformers_cache": values["TRANSFORMERS_CACHE"],
        "torch_home": values["TORCH_HOME"],
        "datalab_cache_dir": values["DATALAB_CACHE_DIR"],
        "surya_model_cache_dir": surya_model_cache_dir,
    }
    return {
        "model_cache_root": values["MODEL_CACHE_ROOT"],
        "effective_hf_home": values["HF_HOME"],
        "effective_hf_hub_cache": values["HF_HUB_CACHE"],
        "effective_transformers_cache": values["TRANSFORMERS_CACHE"],
        "effective_torch_home": values["TORCH_HOME"],
        "effective_datalab_cache_dir": values["DATALAB_CACHE_DIR"],
        "effective_model_cache_dir": values["MODEL_CACHE_DIR"],
        "surya_model_cache_dir": surya_model_cache_dir,
        "surya_required_model_paths": required_paths,
        "surya_required_models_present": bool(required_paths) and all(Path(path).exists() for path in required_paths.values()),
        "c_drive_cache_blocked": any(_is_path_on_c_drive(path) for path in effective_paths.values()),
        "cache_paths_under_model_cache_root": all(
            _is_path_under(path, values["MODEL_CACHE_ROOT"])
            for path in effective_paths.values()
            if path
        ),
    }


def run_surya_ocr_page(
    pdf_path: str | Path,
    pdf_page: int,
    *,
    device: str = "cpu",
    model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT,
    return_words: bool = True,
    allow_download: bool = False,
) -> OcrPageLayout:
    cache = inspect_ocr_model_cache(model_cache_root=model_cache_root)
    if cache["c_drive_cache_blocked"] or not cache["cache_paths_under_model_cache_root"]:
        return _empty_page_layout(
            pdf_path,
            pdf_page,
            model_cache_root,
            error="OCR cache path safety check failed; refusing to use a cache outside model_cache_root or on C drive",
        )
    if not _surya_available():
        return _empty_page_layout(pdf_path, pdf_page, model_cache_root, error="surya OCR modules are not available")
    if not cache["surya_required_models_present"] and not allow_download:
        return _empty_page_layout(
            pdf_path,
            pdf_page,
            model_cache_root,
            error="surya local model cache is unavailable; rerun with allow_download=True to fetch models into model_cache_root",
        )
    if allow_download:
        Path(str(cache["effective_model_cache_dir"])).mkdir(parents=True, exist_ok=True)

    rendered = _render_pdf_page(pdf_path, pdf_page)
    resolved_device = _resolve_device(device)
    try:
        from surya.common.surya.schema import TaskNames
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        foundation_predictor = FoundationPredictor(device=resolved_device)
        detection_predictor = DetectionPredictor(device=resolved_device)
        recognition_predictor = RecognitionPredictor(foundation_predictor)
        predictions = recognition_predictor(
            [rendered["image"]],
            task_names=[TaskNames.ocr_with_boxes],
            det_predictor=detection_predictor,
            highres_images=[rendered["image"]],
            return_words=return_words,
            math_mode=True,
        )
    except Exception as exc:
        return OcrPageLayout(
            pdf_page=pdf_page,
            page_width=rendered["page_width"],
            page_height=rendered["page_height"],
            image_width=rendered["image_width"],
            image_height=rendered["image_height"],
            page_text_layer_length=rendered["page_text_layer_length"],
            lines=[],
            spans=[],
            model_cache_root=str(Path(model_cache_root).resolve()),
            gpu_used=resolved_device == "cuda",
            error=repr(exc),
            traceback_tail="\n".join(traceback.format_exc().splitlines()[-30:]),
        )

    lines: list[PdfLayoutLine] = []
    spans: list[PdfLayoutSpan] = []
    text_lines = getattr(predictions[0], "text_lines", []) if predictions else []
    for line_index, line in enumerate(text_lines):
        text = str(_field(line, "text") or "")
        polygon = _normalize_polygon(_field(line, "polygon"))
        bbox = _bbox_from_polygon(polygon)
        if not bbox:
            continue
        pdf_bbox = transform_bbox_image_to_pdf(
            bbox,
            image_width=rendered["image_width"],
            image_height=rendered["image_height"],
            page_width=rendered["page_width"],
            page_height=rendered["page_height"],
        )
        normalized = normalize_layout_text(text)
        lines.append(
            PdfLayoutLine(
                pdf_page=pdf_page,
                block_index=0,
                line_index=line_index,
                text=text,
                normalized_text=normalized,
                bbox=pdf_bbox,
                source_backend="surya_ocr",
                page_width=rendered["page_width"],
                page_height=rendered["page_height"],
                confidence=_to_optional_float(_field(line, "confidence")),
                text_hash=layout_text_hash(text),
                source_coordinate_space="pdf_points",
            )
        )
        word_like = list(_field(line, "words") or [])
        if not word_like:
            word_like = list(_field(line, "chars") or [])
        for span_index, word in enumerate(word_like):
            span_text = str(_field(word, "text") or "")
            span_polygon = _normalize_polygon(_field(word, "polygon"))
            span_bbox = _bbox_from_polygon(span_polygon)
            if not span_text or not span_bbox:
                continue
            pdf_span_bbox = transform_bbox_image_to_pdf(
                span_bbox,
                image_width=rendered["image_width"],
                image_height=rendered["image_height"],
                page_width=rendered["page_width"],
                page_height=rendered["page_height"],
            )
            spans.append(
                PdfLayoutSpan(
                    pdf_page=pdf_page,
                    block_index=0,
                    line_index=line_index,
                    span_index=span_index,
                    text=span_text,
                    normalized_text=normalize_layout_text(span_text),
                    bbox=pdf_span_bbox,
                    source_backend="surya_ocr",
                    confidence=_to_optional_float(_field(word, "confidence")),
                    text_hash=layout_text_hash(span_text),
                    source_coordinate_space="pdf_points",
                )
            )

    return OcrPageLayout(
        pdf_page=pdf_page,
        page_width=rendered["page_width"],
        page_height=rendered["page_height"],
        image_width=rendered["image_width"],
        image_height=rendered["image_height"],
        page_text_layer_length=rendered["page_text_layer_length"],
        lines=lines,
        spans=spans,
        model_cache_root=str(Path(model_cache_root).resolve()),
        gpu_used=resolved_device == "cuda",
        extracted_text="\n".join(line.text for line in lines),
    )


def transform_bbox_image_to_pdf(
    bbox: dict[str, float],
    *,
    image_width: int,
    image_height: int,
    page_width: float,
    page_height: float,
) -> dict[str, float]:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    scaled = {
        "x0": float(bbox["x0"]) / float(image_width) * float(page_width),
        "y0": float(bbox["y0"]) / float(image_height) * float(page_height),
        "x1": float(bbox["x1"]) / float(image_width) * float(page_width),
        "y1": float(bbox["y1"]) / float(image_height) * float(page_height),
    }
    return _clamp_bbox(scaled, page_width=page_width, page_height=page_height)


def _render_pdf_page(pdf_path: str | Path, pdf_page: int, scale: float = 2.0) -> dict[str, Any]:
    import fitz
    from PIL import Image

    with fitz.open(str(pdf_path)) as document:
        if pdf_page < 1 or pdf_page > document.page_count:
            raise ValueError(f"pdf_page={pdf_page} outside PDF page range")
        page = document.load_page(pdf_page - 1)
        rect = page.rect
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        page_text = page.get_text("text") or ""
        return {
            "image": image,
            "page_width": float(rect.width),
            "page_height": float(rect.height),
            "image_width": int(pixmap.width),
            "image_height": int(pixmap.height),
            "page_text_layer_length": len(page_text),
        }


def _empty_page_layout(
    pdf_path: str | Path,
    pdf_page: int,
    model_cache_root: str | Path,
    *,
    error: str,
) -> OcrPageLayout:
    try:
        rendered = _render_pdf_page(pdf_path, pdf_page)
    except Exception:
        rendered = {
            "page_width": 0.0,
            "page_height": 0.0,
            "image_width": 0,
            "image_height": 0,
            "page_text_layer_length": 0,
        }
    return OcrPageLayout(
        pdf_page=pdf_page,
        page_width=rendered["page_width"],
        page_height=rendered["page_height"],
        image_width=rendered["image_width"],
        image_height=rendered["image_height"],
        page_text_layer_length=rendered["page_text_layer_length"],
        lines=[],
        spans=[],
        model_cache_root=str(Path(model_cache_root).resolve()),
        error=error,
    )


def _resolve_device(device: str) -> str:
    if device != "cuda":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")


def _surya_available() -> bool:
    return all(
        _module_available(module)
        for module in ("surya", "surya.detection", "surya.recognition", "surya.foundation")
    )


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _is_path_on_c_drive(path: str | Path | None) -> bool:
    if not path:
        return False
    return str(path).lower().startswith("c:") or Path(path).drive.lower() == "c:"


def _is_path_under(path: str | Path | None, root: str | Path) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def _normalize_polygon(polygon: Any) -> list[dict[str, float]]:
    if not polygon:
        return []
    points: list[dict[str, float]] = []
    for item in polygon:
        if isinstance(item, dict) and "x" in item and "y" in item:
            points.append({"x": float(item["x"]), "y": float(item["y"])})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append({"x": float(item[0]), "y": float(item[1])})
    return points


def _bbox_from_polygon(polygon: Any) -> dict[str, float] | None:
    points = _normalize_polygon(polygon)
    if not points:
        return None
    return {
        "x0": min(point["x"] for point in points),
        "y0": min(point["y"] for point in points),
        "x1": max(point["x"] for point in points),
        "y1": max(point["y"] for point in points),
    }


def _clamp_bbox(bbox: dict[str, float], *, page_width: float, page_height: float) -> dict[str, float]:
    x0 = max(0.0, min(float(page_width), float(bbox["x0"])))
    y0 = max(0.0, min(float(page_height), float(bbox["y0"])))
    x1 = max(0.0, min(float(page_width), float(bbox["x1"])))
    y1 = max(0.0, min(float(page_height), float(bbox["y1"])))
    return {"x0": min(x0, x1), "y0": min(y0, y1), "x1": max(x0, x1), "y1": max(y0, y1)}


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

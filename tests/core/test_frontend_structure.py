from __future__ import annotations

import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
SOURCE_ROOT = FRONTEND_ROOT / "src"
SOURCE_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".css")
RELATIVE_IMPORT_PATTERNS = (
    re.compile(r"\bfrom\s*[\"'](\.{1,2}/[^\"']+)[\"']"),
    re.compile(r"\bimport\s*\(\s*[\"'](\.{1,2}/[^\"']+)[\"']\s*\)"),
    re.compile(r"\bimport\s*[\"'](\.{1,2}/[^\"']+)[\"']"),
    re.compile(r"@import\s+(?:url\(\s*)?[\"'](\.{1,2}/[^\"']+)[\"']"),
)


def _relative_imports(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return {
        match.group(1)
        for pattern in RELATIVE_IMPORT_PATTERNS
        for match in pattern.finditer(source)
    }


def _resolves(source_path: Path, specifier: str) -> bool:
    clean_specifier = specifier.split("?", 1)[0].split("#", 1)[0]
    target = (source_path.parent / clean_specifier).resolve()
    if target.suffix:
        return target.is_file()
    candidates = [target]
    candidates.extend(target.with_suffix(extension) for extension in SOURCE_EXTENSIONS)
    candidates.extend(target / f"index{extension}" for extension in SOURCE_EXTENSIONS)
    return any(candidate.is_file() for candidate in candidates)


def test_frontend_entrypoints_and_lockfiles_exist() -> None:
    assert SOURCE_ROOT.is_dir()
    assert (SOURCE_ROOT / "main.jsx").is_file()
    assert (SOURCE_ROOT / "App.jsx").is_file()
    assert (FRONTEND_ROOT / "package.json").is_file()
    assert (FRONTEND_ROOT / "package-lock.json").is_file()

    package = json.loads((FRONTEND_ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((FRONTEND_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert package["name"] == "notebook-ai-frontend"
    assert package_lock["name"] == package["name"]
    assert "/src/main.jsx" in (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")


def test_frontend_relative_imports_resolve_without_node_modules() -> None:
    missing: list[str] = []
    source_files = sorted(
        path
        for path in SOURCE_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx", ".css"}
    )
    for source_path in source_files:
        for specifier in sorted(_relative_imports(source_path)):
            if not _resolves(source_path, specifier):
                relative_source = source_path.relative_to(PROJECT_ROOT).as_posix()
                missing.append(f"{relative_source}: {specifier}")

    assert missing == []


from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = PROJECT_ROOT / "zotero-plugin"
DEFAULT_BUILD_DIR = PLUGIN_ROOT / "build"
EXCLUDED_DIRS = {"build", "node_modules", ".git", "__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}


@dataclass(frozen=True)
class PackageReport:
    plugin_root: str
    output_path: str
    files_packaged: list[str]
    excluded_dirs: list[str]
    excluded_suffixes: list[str]
    manifest_id: str
    manifest_name: str
    manifest_version: str
    package_created: bool


def package_plugin(
    plugin_root: str | Path = PLUGIN_ROOT,
    *,
    output_path: str | Path | None = None,
) -> PackageReport:
    root = Path(plugin_root)
    manifest = _read_manifest(root)
    build_dir = root / "build"
    output = Path(output_path) if output_path is not None else build_dir / _package_name(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = _package_files(root)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(root / relative, relative.as_posix())
    return PackageReport(
        plugin_root=str(root),
        output_path=str(output),
        files_packaged=[path.as_posix() for path in files],
        excluded_dirs=sorted(EXCLUDED_DIRS),
        excluded_suffixes=sorted(EXCLUDED_SUFFIXES),
        manifest_id=manifest["applications"]["zotero"]["id"],
        manifest_name=manifest["name"],
        manifest_version=manifest["version"],
        package_created=True,
    )


def _read_manifest(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _package_name(manifest: dict) -> str:
    version = str(manifest["version"]).replace("/", "-")
    return f"notebook-ai-inspiration-{version}.xpi"


def _package_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_dir():
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package the NOTEBOOK_AI Zotero inspiration plugin as .xpi.")
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = package_plugin(args.plugin_root, output_path=args.output)
    payload = asdict(report)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"output_path={report.output_path}")
        print(f"files_packaged={len(report.files_packaged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

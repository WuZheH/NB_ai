"""Phase 18E: ChatGPT Object/Tag Input Bundle Service.

Generates a single chatgpt_object_tag_input.md file that bundles:
1. The prompt template (from docs/Phase18E_ChatGPTObjectTagPrompt0.md)
2. paper.md
3. notes.md
4. import_manifest.json
5. source_trace.json

This bundle is intended to be sent to ChatGPT to produce
object_tag_suggestions_v1 JSON.

No DB writes. No LLM calls.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.paths import PROJECT_ROOT
from app.services.import_preview_service import (
    ImportPreviewError,
    _existing_job_dir,
    _read_json,
    _relative,
    _safety_response,
)

PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "docs" / "Phase18E_ChatGPTObjectTagPrompt0.md"
BUNDLE_FILE = "chatgpt_object_tag_input.md"

# Extract the prompt template from the doc:
# The actual prompt is inside a ````text ... ```` code block.
_PROMPT_BLOCK_RE = re.compile(r"````text\s*\n(.*?)\n````", re.DOTALL)


def _extract_prompt_template() -> str:
    """Extract the ChatGPT prompt template from the doc file."""
    if not PROMPT_TEMPLATE_PATH.is_file():
        raise ImportPreviewError(
            f"Prompt template not found: {_relative(PROMPT_TEMPLATE_PATH)}. "
            "Ensure docs/Phase18E_ChatGPTObjectTagPrompt0.md exists."
        )
    doc_text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    match = _PROMPT_BLOCK_RE.search(doc_text)
    if not match:
        raise ImportPreviewError(
            "Prompt template block (````text ... ````) not found in "
            f"{_relative(PROMPT_TEMPLATE_PATH)}."
        )
    return match.group(1).strip()


def generate_chatgpt_bundle(import_job_id: str) -> dict[str, Any]:
    """Generate the ChatGPT object/tag input bundle file.

    Reads staging files and bundles them with the prompt template.
    Returns metadata about the bundle (no raw paths exposed to frontend).
    """
    job_dir = _existing_job_dir(import_job_id)

    paper_md_path = job_dir / "paper.md"
    notes_md_path = job_dir / "notes.md"
    manifest_path = job_dir / "import_manifest.json"
    source_trace_path = job_dir / "source_trace.json"
    bundle_path = job_dir / BUNDLE_FILE

    # Validate required files
    if not paper_md_path.is_file():
        raise ImportPreviewError("paper.md not found in import staging.")
    if not source_trace_path.is_file():
        raise ImportPreviewError("source_trace.json not found in import staging.")
    if not manifest_path.is_file():
        raise ImportPreviewError("import_manifest.json not found in import staging.")

    # Read inputs
    prompt_template = _extract_prompt_template()
    paper_md = paper_md_path.read_text(encoding="utf-8")
    notes_md = notes_md_path.read_text(encoding="utf-8") if notes_md_path.is_file() else "(empty)"
    manifest = _read_json(manifest_path)
    source_trace = _read_json(source_trace_path)

    # Sanitize: remove raw_local_path_returned_to_frontend field
    source_trace.pop("raw_local_path_returned_to_frontend", None)

    # Build bundle
    import json as _json
    bundle_content = f"""# ChatGPT Object/Tag Suggestion Input

请严格按照下方 Prompt Template 的要求，基于 paper.md、notes.md、import_manifest.json、source_trace.json 输出 object_tag_suggestions_v1 JSON。

只输出 JSON。
不要输出 Markdown。
不要输出解释。
不要生成 relation。
不要生成 hypothesis。
所有对象 status 必须是 suggested。

===== BEGIN PROMPT TEMPLATE =====

{prompt_template}

===== END PROMPT TEMPLATE =====

===== BEGIN PAPER_MD =====

{paper_md}

===== END PAPER_MD =====

===== BEGIN NOTES_MD =====

{notes_md}

===== END NOTES_MD =====

===== BEGIN IMPORT_MANIFEST_JSON =====

{_json.dumps(manifest, ensure_ascii=False, indent=2)}

===== END IMPORT_MANIFEST_JSON =====

===== BEGIN SOURCE_TRACE_JSON =====

{_json.dumps(source_trace, ensure_ascii=False, indent=2)}

===== END SOURCE_TRACE_JSON =====
"""

    bundle_path.write_text(bundle_content, encoding="utf-8")

    # Preview: first 500 chars
    preview = bundle_content[:500]
    if len(bundle_content) > 500:
        preview = preview.rstrip() + "\n\n... (truncated)"

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "bundle_path": _relative(bundle_path),
        "bundle_preview": preview,
        "bundle_size_chars": len(bundle_content),
        "prompt_template_source": _relative(PROMPT_TEMPLATE_PATH),
        **_safety_response(),
    }


def get_chatgpt_bundle_content(import_job_id: str) -> dict[str, Any]:
    """Return the full chatgpt_object_tag_input.md content."""
    job_dir = _existing_job_dir(import_job_id)
    bundle_path = job_dir / BUNDLE_FILE

    if not bundle_path.is_file():
        raise ImportPreviewError(
            "chatgpt_object_tag_input.md not found. "
            "Run POST /chatgpt-object-tag-input first."
        )

    content = bundle_path.read_text(encoding="utf-8")

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "bundle_path": _relative(bundle_path),
        "bundle_content": content,
        "bundle_size_chars": len(content),
        **_safety_response(),
    }

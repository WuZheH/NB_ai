from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "start_quick_tunnel.ps1"


def test_quick_tunnel_script_is_manual_hidden_and_non_mutating() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (
        "SEARCH_CLOUDFLARED",
        "SEARCH_TUNNEL_STATE_DIR",
        "http://127.0.0.1:8787",
        ".codex_tmp\\quick-tunnel",
        "Start-Process",
        'WindowStyle = "Hidden"',
        "--no-autoupdate",
        "quick_tunnel_online",
        'mcp_url = "$publicUrl/mcp"',
        "chatgpt_configuration_changed = $false",
        "[switch]$AllowParallel",
        "[switch]$Check",
    ):
        assert token in source
    for forbidden in (
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "Stop-Process",
        "taskkill",
        "Remove-Item",
        "Set-ItemProperty",
        "New-ItemProperty",
        "setx",
    ):
        assert forbidden not in source


def test_quick_tunnel_script_never_downloads_or_persists_credentials() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert re.search(r"(?mi)^\s*(curl|wget|winget)\b", source) is None
    for forbidden in (
        "pip install",
        "npm install",
        "credentials-file",
        "cert.pem",
        "tunnel token",
    ):
        assert forbidden not in source

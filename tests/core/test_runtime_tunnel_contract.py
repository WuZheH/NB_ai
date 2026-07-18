from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from app.runtime.config import RuntimeConfig
from app.runtime.contracts import (
    RUNTIME_SCHEMA_VERSION,
    RuntimeState,
    RuntimeStatus,
    TunnelState,
)
from app.runtime.health import HealthResult
from app.runtime.tunnel import CloudflareTunnelProbe


def _runtime_env(tmp_path: Path) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
        "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
        "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
    }


def _config(tmp_path: Path) -> RuntimeConfig:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return RuntimeConfig.load(project_root=project, env=_runtime_env(tmp_path))


def test_cloudflare_quick_tunnel_probe_is_read_only_and_reports_online(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cloudflared.log"
    log.write_text(
        "Your quick Tunnel has been created https://temporary.trycloudflare.com\n"
        "Registered tunnel connection\n",
        encoding="utf-8",
    )
    probe = CloudflareTunnelProbe(
        _config(tmp_path),
        env={"USERPROFILE": str(tmp_path)},
        process_reader=lambda: [
            {
                "ProcessId": 43120,
                "ExecutablePath": str(tmp_path / "cloudflared.exe"),
                "CommandLine": (
                    f'cloudflared.exe tunnel --url http://127.0.0.1:8787 '
                    f'--logfile "{log}"'
                ),
            }
        ],
    )
    monkeypatch.setattr(probe, "_public_health", lambda url: HealthResult(True))
    result = probe.diagnose()
    assert result.tunnel_type == "quick"
    assert result.state is TunnelState.QUICK_ONLINE
    assert result.pid == 43120
    assert result.public_url == "https://temporary.trycloudflare.com"
    assert result.named_tunnel_configured is False


def test_cloudflare_quick_tunnel_offline_is_not_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cloudflared.log"
    log.write_text("https://temporary.trycloudflare.com\n", encoding="utf-8")
    processes = [
        {
            "ProcessId": 43120,
            "ExecutablePath": str(tmp_path / "cloudflared.exe"),
            "CommandLine": f'cloudflared.exe tunnel --url http://127.0.0.1:8787 --logfile "{log}"',
        }
    ]
    probe = CloudflareTunnelProbe(
        _config(tmp_path),
        env={"USERPROFILE": str(tmp_path)},
        process_reader=lambda: processes,
    )
    monkeypatch.setattr(probe, "_public_health", lambda url: HealthResult(False))
    result = probe.diagnose()
    assert result.state is TunnelState.UNHEALTHY
    assert result.error_code == "quick_tunnel_unreachable"
    assert len(processes) == 1


def test_cloudflare_process_probe_hides_direct_powershell_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, stdout=b"[]")

    monkeypatch.setattr(subprocess, "run", run)
    assert CloudflareTunnelProbe._windows_cloudflared_processes() == []
    if os.name == "nt":
        assert captured["arguments"][0] == "powershell.exe"
        assert captured["shell"] is False
        flags = int(captured["creationflags"])
        assert flags & subprocess.CREATE_NO_WINDOW
        assert not flags & subprocess.DETACHED_PROCESS
        startup_info = captured["startupinfo"]
        assert startup_info.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert startup_info.wShowWindow == subprocess.SW_HIDE


def test_named_tunnel_probe_validates_config_credentials_and_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloudflare = tmp_path / ".cloudflared"
    cloudflare.mkdir()
    credentials = cloudflare / "named.json"
    credentials.write_text("{}", encoding="utf-8")
    (cloudflare / "config.yml").write_text(
        "tunnel: fixed-id\n"
        f"credentials-file: {credentials}\n"
        "ingress:\n"
        "  - hostname: search.example.test\n"
        "    service: http://127.0.0.1:8787\n",
        encoding="utf-8",
    )
    probe = CloudflareTunnelProbe(
        _config(tmp_path),
        env={"USERPROFILE": str(tmp_path)},
        process_reader=lambda: [],
    )
    monkeypatch.setattr(probe, "_public_health", lambda url: HealthResult(True))
    result = probe.diagnose()
    assert result.tunnel_type == "named"
    assert result.state is TunnelState.PERSISTENT_ONLINE
    assert result.public_url == "https://search.example.test"
    assert result.credentials_present is True
    assert result.named_tunnel_configured is True


def test_unconfigured_tunnel_probe_reports_read_only_none_state(tmp_path: Path) -> None:
    result = CloudflareTunnelProbe(
        _config(tmp_path),
        env={"USERPROFILE": str(tmp_path)},
        process_reader=lambda: [],
    ).diagnose()
    assert result.tunnel_type == "none"
    assert result.state is TunnelState.NOT_CONFIGURED
    assert result.error_code == "persistent_tunnel_not_configured"
    assert result.credentials_present is False


def test_legacy_runtime_tunnel_field_is_ignored_without_rewriting_file(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.paths.config_dir.mkdir(parents=True)
    payload = {
        **config.to_persisted_dict(),
        "tunnel": {
            "driver": "openai_secure_tunnel",
            "tunnel_id": "legacy-id",
            "profile": "legacy-profile",
            "client_path": "tunnel-client.exe",
            "ready_url": "http://127.0.0.1:9494/readyz",
        },
    }
    config.paths.runtime_config_file.write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )
    before = config.paths.runtime_config_file.read_bytes()

    loaded = RuntimeConfig.load(
        project_root=config.paths.project_root,
        env=_runtime_env(tmp_path),
    )

    assert not hasattr(loaded, "tunnel")
    assert "tunnel" not in loaded.to_persisted_dict()
    assert config.paths.runtime_config_file.read_bytes() == before


def test_runtime_config_without_legacy_tunnel_field_loads_canonical_backends(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    payload = config.to_persisted_dict()
    assert "tunnel" not in payload
    assert config.backend_port == 8000
    assert config.mcp_port == 8787
    assert not hasattr(config, "tunnel")


@pytest.mark.parametrize("legacy_state", list(TunnelState))
def test_all_legacy_tunnel_states_still_parse(legacy_state: TunnelState) -> None:
    status = RuntimeStatus.from_dict(
        {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "state": RuntimeState.LOCAL_READY_TUNNEL_MISSING.value,
            "updated_at": "2026-07-18T00:00:00+00:00",
            "tunnel_state": legacy_state.value,
            "components": {},
        }
    )
    assert status.tunnel_state is legacy_state


def test_packaged_runtime_has_no_managed_tunnel_subsystem_references() -> None:
    root = Path(__file__).resolve().parents[2]
    product_roots = (
        root / "app" / "runtime",
        root / "integrations" / "search_desktop" / "src",
        root / "integrations" / "search_desktop" / "scripts",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for product_root in product_roots
        if product_root.exists()
        for path in product_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".py", ".js", ".cjs", ".mjs"}
    )
    for obsolete in (
        "TunnelDriverBoundary",
        "TunnelConfig",
        "configure-tunnel",
        "tunnel-client",
    ):
        assert obsolete not in source


def test_runtime_config_rejects_plaintext_secret_fields(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.paths.config_dir.mkdir(parents=True)
    config.paths.runtime_config_file.write_text(
        json.dumps(
            {
                **config.to_persisted_dict(),
                "external_service": {"api_key": "must-not-be-stored"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="runtime_config_contains_forbidden_secret"):
        RuntimeConfig.load(
            project_root=config.paths.project_root,
            env=_runtime_env(tmp_path),
        )

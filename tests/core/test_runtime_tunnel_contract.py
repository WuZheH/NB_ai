from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from app.runtime.config import RuntimeConfig, TunnelConfig
from app.runtime.contracts import TunnelDriver, TunnelState
from app.runtime.health import HealthResult
from app.runtime.tunnel import CloudflareTunnelProbe, TunnelDriverBoundary


def _config(tmp_path: Path, tunnel: TunnelConfig) -> RuntimeConfig:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    base = RuntimeConfig.load(
        project_root=project,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
        },
    )
    return replace(base, tunnel=tunnel)


def test_secure_tunnel_missing_client_has_explicit_state(tmp_path: Path) -> None:
    config = _config(tmp_path, TunnelConfig(tunnel_id="tunnel-1"))
    diagnosis = TunnelDriverBoundary(config).diagnose()
    assert diagnosis.state is TunnelState.CLIENT_MISSING
    assert diagnosis.error_code == "tunnel_client_missing"


def test_secure_tunnel_missing_id_is_distinct_from_auth(
    tmp_path: Path,
) -> None:
    client = tmp_path / "tunnel-client.exe"
    client.write_bytes(b"")
    config = _config(tmp_path, TunnelConfig(client_path=str(client)))
    diagnosis = TunnelDriverBoundary(config).diagnose()
    assert diagnosis.state is TunnelState.ID_MISSING
    assert diagnosis.error_code == "tunnel_id_missing"


def test_secure_tunnel_doctor_failure_reports_profile_or_auth_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "tunnel-client.exe"
    client.write_bytes(b"")
    config = _config(
        tmp_path,
        TunnelConfig(tunnel_id="tunnel-1", client_path=str(client)),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1),
    )
    diagnosis = TunnelDriverBoundary(config).diagnose()
    assert diagnosis.state is TunnelState.AUTH_MISSING
    assert diagnosis.error_code == "tunnel_profile_or_auth_not_ready"


def test_secure_tunnel_process_spec_contains_no_secret_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "tunnel-client.exe"
    client.write_bytes(b"")
    config = _config(
        tmp_path,
        TunnelConfig(
            tunnel_id="tunnel-1",
            profile="notebook-ai",
            client_path=str(client),
        ),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )
    spec = TunnelDriverBoundary(config).process_spec()
    assert spec.arguments == ("run", "--profile", "notebook-ai")
    serialized = " ".join([*spec.arguments, *spec.environment.keys()]).lower()
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert spec.environment == {}
    assert config.tunnel_target.endswith("/mcp")


def test_secure_tunnel_uses_documented_profile_commands_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "tunnel-client.exe"
    client.write_bytes(b"")
    config = _config(
        tmp_path,
        TunnelConfig(
            tunnel_id="tunnel-1",
            profile="notebook-ai",
            client_path=str(client),
        ),
    )
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(subprocess, "run", run)
    boundary = TunnelDriverBoundary(config)
    boundary.initialize_profile()
    assert calls[0] == [
        str(client.resolve()),
        "init",
        "--sample",
        "sample_mcp_stdio_local",
        "--profile",
        "notebook-ai",
        "--tunnel-id",
        "tunnel-1",
        "--mcp-server-url",
        "http://127.0.0.1:8787/mcp",
    ]
    boundary.diagnose(run_doctor=True)
    assert calls[1] == [
        str(client.resolve()),
        "doctor",
        "--profile",
        "notebook-ai",
        "--explain",
    ]
    assert boundary.process_spec().arguments == (
        "run",
        "--profile",
        "notebook-ai",
    )
    serialized = " ".join(value for call in calls for value in call).lower()
    assert "control_plane_api_key" not in serialized
    assert "authorization" not in serialized


def test_secure_tunnel_rejects_unexpected_executable_name(tmp_path: Path) -> None:
    client = tmp_path / "unknown-client.exe"
    client.write_bytes(b"")
    config = _config(
        tmp_path,
        TunnelConfig(tunnel_id="tunnel-1", client_path=str(client)),
    )
    diagnosis = TunnelDriverBoundary(config).diagnose()
    assert diagnosis.state is TunnelState.CLIENT_MISSING


def test_cloudflare_quick_tunnel_is_manual_only_and_never_has_process_spec(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        TunnelConfig(driver=TunnelDriver.CLOUDFLARE_QUICK_DEV),
    )
    boundary = TunnelDriverBoundary(config)
    diagnosis = boundary.diagnose()
    assert diagnosis.state is TunnelState.NOT_CONFIGURED
    assert diagnosis.error_code == "quick_tunnel_manual_dev_only"
    with pytest.raises(RuntimeError, match="quick_tunnel_manual_dev_only"):
        boundary.process_spec()


def test_cloudflare_quick_tunnel_probe_is_read_only_and_reports_online(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cloudflared.log"
    log.write_text(
        "Your quick Tunnel has been created https://temporary.trycloudflare.com\n"
        "Registered tunnel connection\n",
        encoding="utf-8",
    )
    config = _config(tmp_path, TunnelConfig())
    probe = CloudflareTunnelProbe(
        config,
        env={"USERPROFILE": str(tmp_path)},
        process_reader=lambda: [
            {
                "ProcessId": 40036,
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
    assert result.pid == 40036
    assert result.public_url == "https://temporary.trycloudflare.com"
    assert result.named_tunnel_configured is False


def test_cloudflare_quick_tunnel_offline_is_not_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "cloudflared.log"
    log.write_text("https://temporary.trycloudflare.com\n", encoding="utf-8")
    config = _config(tmp_path, TunnelConfig())
    processes = [
        {
            "ProcessId": 40036,
            "ExecutablePath": str(tmp_path / "cloudflared.exe"),
            "CommandLine": f'cloudflared.exe tunnel --url http://127.0.0.1:8787 --logfile "{log}"',
        }
    ]
    probe = CloudflareTunnelProbe(
        config,
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
        _config(tmp_path, TunnelConfig()),
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


def test_none_driver_is_an_explicit_local_only_mode(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        TunnelConfig(driver=TunnelDriver.NONE),
    )
    diagnosis = TunnelDriverBoundary(config).diagnose()
    assert diagnosis.state is TunnelState.NOT_CONFIGURED
    assert diagnosis.error_code == "tunnel_disabled"


def test_persisted_tunnel_config_never_contains_authentication(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, TunnelConfig()).with_tunnel(
        tunnel_id="fixed-tunnel",
        profile="notebook-ai",
    )
    payload = config.to_persisted_dict()
    assert payload["tunnel"]["tunnel_id"] == "fixed-tunnel"
    assert not set(payload["tunnel"]).intersection(
        {"api_key", "token", "secret", "authorization"}
    )


def test_tunnel_ready_url_must_be_explicit_loopback(tmp_path: Path) -> None:
    config = _config(tmp_path, TunnelConfig()).with_tunnel(
        tunnel_id="fixed-tunnel",
        profile="notebook-ai",
        ready_url="http://127.0.0.1:9494/readyz",
    )
    assert config.tunnel.ready_url == "http://127.0.0.1:9494/readyz"
    with pytest.raises(ValueError, match="loopback"):
        _config(
            tmp_path,
            TunnelConfig.from_dict(
                {
                    "tunnel_id": "fixed-tunnel",
                    "ready_url": "https://example.com/ready",
                }
            ),
        )
    with pytest.raises(ValueError, match="loopback"):
        TunnelConfig.from_dict(
            {
                "tunnel_id": "fixed-tunnel",
                "ready_url": "http://127.0.0.1:9494/readyz?token=forbidden",
            }
        )


def test_configure_command_does_not_automatically_initialize_client() -> None:
    source = (Path(__file__).resolve().parents[2] / "app" / "runtime" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert ".initialize_profile(" not in source
    assert "documented_init_required" in source


def test_runtime_config_rejects_plaintext_secret_fields(tmp_path: Path) -> None:
    config = _config(tmp_path, TunnelConfig())
    config.paths.config_dir.mkdir(parents=True)
    config.paths.runtime_config_file.write_text(
        json.dumps(
            {
                **config.to_persisted_dict(),
                "tunnel": {
                    **config.tunnel.to_dict(),
                    "api_key": "must-not-be-stored",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="runtime_config_contains_forbidden_secret"):
        RuntimeConfig.load(
            project_root=config.paths.project_root,
            env={
                "LOCALAPPDATA": str(config.paths.local_app_data.parent),
                "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
                "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
            },
        )

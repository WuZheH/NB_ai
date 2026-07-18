from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping
from app.runtime.config import RuntimeConfig
from app.runtime.contracts import TunnelState
from app.runtime.health import HealthResult, check_json_health
from app.runtime.process_manager import hidden_windows_subprocess_options


@dataclass(frozen=True)
class ChatGptTunnelStatus:
    tunnel_type: str
    state: TunnelState
    error_code: str | None = None
    pid: int | None = None
    public_url: str | None = None
    config_path: str | None = None
    credentials_present: bool = False
    named_tunnel_configured: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "tunnel_type": self.tunnel_type,
            "state": self.state.value,
            "error_code": self.error_code,
            "pid": self.pid,
            "public_url": self.public_url,
            "config_path": self.config_path,
            "credentials_present": self.credentials_present,
            "named_tunnel_configured": self.named_tunnel_configured,
        }


class CloudflareTunnelProbe:
    """Read-only Cloudflare status probe; it never creates or starts a tunnel."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        env: Mapping[str, str] | None = None,
        process_reader: Callable[[], list[dict[str, object]]] | None = None,
    ):
        self.config = config
        self.env = os.environ if env is None else env
        self.process_reader = process_reader or self._windows_cloudflared_processes

    def diagnose(self) -> ChatGptTunnelStatus:
        named = self._named_tunnel()
        if named is not None:
            return named
        quick = self._quick_tunnel()
        if quick is not None:
            return quick
        return ChatGptTunnelStatus(
            tunnel_type="none",
            state=TunnelState.NOT_CONFIGURED,
            error_code="persistent_tunnel_not_configured",
        )

    def _named_tunnel(self) -> ChatGptTunnelStatus | None:
        home = self.env.get("USERPROFILE") or self.env.get("HOME")
        if not home:
            return None
        directory = Path(home).expanduser() / ".cloudflared"
        config_path = next(
            (path for path in (directory / "config.yml", directory / "config.yaml") if path.is_file()),
            None,
        )
        if config_path is None:
            return None
        try:
            source = config_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ChatGptTunnelStatus(
                tunnel_type="named",
                state=TunnelState.UNHEALTHY,
                error_code="named_tunnel_config_unreadable",
                config_path=str(config_path),
                named_tunnel_configured=True,
            )
        tunnel_configured = bool(re.search(r"(?mi)^\s*tunnel\s*:\s*\S+", source))
        hostname_match = re.search(
            r"(?mi)^\s*(?:-\s*)?hostname\s*:\s*['\"]?([^\s'\"]+)",
            source,
        )
        credentials_match = re.search(r"(?mi)^\s*credentials-file\s*:\s*(.+?)\s*$", source)
        credential_exists = False
        if credentials_match:
            raw = credentials_match.group(1).strip().strip("'\"")
            expanded = os.path.expandvars(os.path.expanduser(raw))
            credential_path = Path(expanded)
            if not credential_path.is_absolute():
                credential_path = config_path.parent / credential_path
            credential_exists = credential_path.is_file()
        hostname = hostname_match.group(1).strip() if hostname_match else None
        public_url = f"https://{hostname}" if hostname else None
        online = bool(public_url and self._public_health(public_url).ready)
        return ChatGptTunnelStatus(
            tunnel_type="named",
            state=(
                TunnelState.PERSISTENT_ONLINE
                if online
                else TunnelState.PERSISTENT_CONFIGURED
            ),
            error_code=(
                None
                if online
                else "named_tunnel_not_running"
                if tunnel_configured and credential_exists
                else "named_tunnel_configuration_incomplete"
            ),
            public_url=public_url,
            config_path=str(config_path),
            credentials_present=credential_exists,
            named_tunnel_configured=tunnel_configured,
        )

    def _quick_tunnel(self) -> ChatGptTunnelStatus | None:
        expected_target = f"http://127.0.0.1:{self.config.mcp_port}"
        for process in self.process_reader():
            command = str(process.get("CommandLine") or process.get("commandLine") or "")
            executable = str(process.get("ExecutablePath") or process.get("executablePath") or "")
            if Path(executable).name.casefold() != "cloudflared.exe":
                continue
            if " tunnel " not in f" {command.casefold()} " or expected_target.casefold() not in command.casefold():
                continue
            pid_value = process.get("ProcessId") or process.get("pid")
            pid = int(pid_value) if pid_value is not None else None
            public_url = self._quick_url_from_command_log(command)
            ready = bool(public_url and self._public_health(public_url).ready)
            return ChatGptTunnelStatus(
                tunnel_type="quick",
                state=TunnelState.QUICK_ONLINE if ready else TunnelState.UNHEALTHY,
                error_code=None if ready else "quick_tunnel_unreachable",
                pid=pid,
                public_url=public_url,
            )
        return None

    def _quick_url_from_command_log(self, command: str) -> str | None:
        match = re.search(r"--logfile(?:=|\s+)(?:\"([^\"]+)\"|(\S+))", command, re.I)
        if not match:
            return None
        path = Path(match.group(1) or match.group(2))
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 262_144), os.SEEK_SET)
                tail = stream.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        urls = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", tail, re.I)
        return urls[-1].rstrip("/") if urls else None

    def _public_health(self, public_url: str) -> HealthResult:
        return check_json_health(
            f"{public_url.rstrip('/')}/healthz",
            validator=lambda value: value.get("status") == "ok"
            and value.get("service") == "notebook-ai-mcp",
            timeout_seconds=2.0,
        )

    @staticmethod
    def _windows_cloudflared_processes() -> list[dict[str, object]]:
        if os.name != "nt":
            return []
        command = (
            "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
            "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                shell=False,
                **hidden_windows_subprocess_options(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0 or not result.stdout.strip():
            return []
        try:
            value = json.loads(result.stdout.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        if isinstance(value, dict):
            return [value]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

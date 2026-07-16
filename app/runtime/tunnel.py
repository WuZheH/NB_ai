from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Mapping
from app.runtime.config import RuntimeConfig
from app.runtime.contracts import TunnelDriver, TunnelState
from app.runtime.health import HealthResult, check_http_ready, check_json_health
from app.runtime.process_manager import ProcessSpec, hidden_windows_subprocess_options


@dataclass(frozen=True)
class TunnelDiagnosis:
    driver: TunnelDriver
    state: TunnelState
    error_code: str | None = None
    client_path: str | None = None
    tunnel_id_configured: bool = False
    profile: str | None = None
    target: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "driver": self.driver.value,
            "state": self.state.value,
            "error_code": self.error_code,
            "client_path": self.client_path,
            "tunnel_id_configured": self.tunnel_id_configured,
            "profile": self.profile,
            "target": self.target,
        }


class TunnelDriverBoundary:
    """Secure-tunnel boundary that never handles or persists authentication."""

    def __init__(self, config: RuntimeConfig):
        self.config = config

    def diagnose(self, *, run_doctor: bool = True) -> TunnelDiagnosis:
        driver = self.config.tunnel.driver
        if driver is TunnelDriver.NONE:
            return TunnelDiagnosis(
                driver=driver,
                state=TunnelState.NOT_CONFIGURED,
                error_code="tunnel_disabled",
                target=self.config.tunnel_target,
            )
        if driver is TunnelDriver.CLOUDFLARE_QUICK_DEV:
            return TunnelDiagnosis(
                driver=driver,
                state=TunnelState.NOT_CONFIGURED,
                error_code="quick_tunnel_manual_dev_only",
                target=self.config.tunnel_target,
            )
        executable = self._client_executable()
        if executable is None:
            return self._diagnosis(
                TunnelState.CLIENT_MISSING,
                error_code="tunnel_client_missing",
            )
        if not self.config.tunnel.tunnel_id:
            return self._diagnosis(
                TunnelState.ID_MISSING,
                error_code="tunnel_id_missing",
                executable=executable,
            )
        if run_doctor:
            try:
                result = subprocess.run(
                    [
                        str(executable),
                        "doctor",
                        "--profile",
                        self.config.tunnel.profile,
                        "--explain",
                    ],
                    cwd=str(self.config.paths.project_root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    check=False,
                    shell=False,
                    **hidden_windows_subprocess_options(),
                )
            except (OSError, subprocess.TimeoutExpired):
                return self._diagnosis(
                    TunnelState.UNHEALTHY,
                    error_code="tunnel_doctor_failed",
                    executable=executable,
                )
            if result.returncode != 0:
                return self._diagnosis(
                    TunnelState.AUTH_MISSING,
                    error_code="tunnel_profile_or_auth_not_ready",
                    executable=executable,
                )
        return self._diagnosis(
            TunnelState.STARTING,
            executable=executable,
        )

    def process_spec(self, *, verify_profile: bool = True) -> ProcessSpec:
        diagnosis = self.diagnose(run_doctor=verify_profile)
        if diagnosis.state is not TunnelState.STARTING:
            raise RuntimeError(diagnosis.error_code or diagnosis.state.value)
        executable = Path(diagnosis.client_path or "")
        return ProcessSpec(
            name="tunnel",
            executable=executable,
            arguments=("run", "--profile", self.config.tunnel.profile),
            cwd=self.config.paths.project_root,
            environment={},
        )

    def initialize_profile(self) -> None:
        """Create/update the named profile without accepting or persisting auth."""

        executable = self._client_executable()
        if executable is None:
            raise RuntimeError("tunnel_client_missing")
        tunnel_id = self.config.tunnel.tunnel_id
        if not tunnel_id:
            raise RuntimeError("tunnel_id_missing")
        try:
            result = subprocess.run(
                [
                    str(executable),
                    "init",
                    "--sample",
                    "sample_mcp_stdio_local",
                    "--profile",
                    self.config.tunnel.profile,
                    "--tunnel-id",
                    tunnel_id,
                    "--mcp-server-url",
                    self.config.tunnel_target,
                ],
                cwd=str(self.config.paths.project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
                shell=False,
                **hidden_windows_subprocess_options(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("tunnel_init_failed") from exc
        if result.returncode != 0:
            raise RuntimeError("tunnel_init_failed")

    def readiness(self) -> HealthResult:
        ready_url = self.config.tunnel.ready_url
        if not ready_url:
            return HealthResult(False, "tunnel_readiness_unverified")
        return check_http_ready(ready_url)

    def _client_executable(self) -> Path | None:
        configured = self.config.tunnel.client_path
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.name.casefold() not in {"tunnel-client", "tunnel-client.exe"}:
                return None
            return candidate.resolve() if candidate.is_file() else None
        located = shutil.which("tunnel-client")
        if not located:
            return None
        candidate = Path(located).resolve()
        return (
            candidate
            if candidate.name.casefold() in {"tunnel-client", "tunnel-client.exe"}
            else None
        )

    def _diagnosis(
        self,
        state: TunnelState,
        *,
        error_code: str | None = None,
        executable: Path | None = None,
    ) -> TunnelDiagnosis:
        return TunnelDiagnosis(
            driver=self.config.tunnel.driver,
            state=state,
            error_code=error_code,
            client_path=str(executable) if executable else None,
            tunnel_id_configured=bool(self.config.tunnel.tunnel_id),
            profile=self.config.tunnel.profile,
            target=self.config.tunnel_target,
        )


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

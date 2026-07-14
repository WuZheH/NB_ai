from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from app.runtime.config import RuntimeConfig
from app.runtime.contracts import TunnelDriver, TunnelState
from app.runtime.health import HealthResult, check_http_ready
from app.runtime.process_manager import ProcessSpec


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

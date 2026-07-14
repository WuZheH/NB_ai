from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from app.core.paths import PROJECT_ROOT
from app.runtime.contracts import TunnelDriver


RUNTIME_CONFIG_SCHEMA_VERSION = "notebook_ai.runtime.config.v1"
# The launcher is executed by the desired Python environment.  Local runtime
# configuration or NOTEBOOK_AI_PYTHON_EXE can still select another interpreter
# explicitly, without baking a machine-specific Conda path into source control.
DEFAULT_PYTHON_EXE = Path(sys.executable)
DEFAULT_BACKEND_PORT = 8000
DEFAULT_MCP_PORT = 8787
DEFAULT_TUNNEL_PROFILE = "notebook-ai"
DEFAULT_TUNNEL_TARGET = "http://127.0.0.1:8787/mcp"
FORBIDDEN_SECRET_CONFIG_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "password", "secret", "token"}
)


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    local_app_data: Path
    runtime_dir: Path
    logs_dir: Path
    config_dir: Path
    status_file: Path
    supervisor_lock: Path
    supervisor_stop_file: Path
    control_dir: Path
    runtime_config_file: Path
    runtime_log_file: Path
    launcher_script: Path
    note_status_script: Path
    note_sync_script: Path
    mcp_app_dir: Path
    mcp_server_entry: Path

    @classmethod
    def resolve(
        cls,
        *,
        project_root: str | Path = PROJECT_ROOT,
        env: Mapping[str, str] | None = None,
    ) -> "RuntimePaths":
        environment = os.environ if env is None else env
        local_value = environment.get("LOCALAPPDATA")
        if not local_value:
            raise RuntimeError("LOCALAPPDATA is required for the Windows runtime launcher")
        root = Path(project_root).resolve()
        local_root = Path(local_value).expanduser().resolve() / "NOTEBOOK_AI"
        runtime_dir = local_root / "runtime"
        logs_dir = local_root / "logs"
        config_dir = local_root / "config"
        return cls(
            project_root=root,
            local_app_data=local_root,
            runtime_dir=runtime_dir,
            logs_dir=logs_dir,
            config_dir=config_dir,
            status_file=runtime_dir / "status.json",
            supervisor_lock=runtime_dir / "supervisor.lock",
            supervisor_stop_file=runtime_dir / "stop.request",
            control_dir=runtime_dir / "control",
            runtime_config_file=config_dir / "runtime.json",
            runtime_log_file=logs_dir / "runtime.jsonl",
            launcher_script=root / "scripts" / "runtime" / "notebook_ai_launcher.py",
            note_status_script=root / "scripts" / "index" / "status_zotero_note_vectors.py",
            note_sync_script=root / "scripts" / "index" / "sync_zotero_note_vectors.py",
            mcp_app_dir=root / "integrations" / "notebook_ai_chatgpt_app",
            mcp_server_entry=(
                root
                / "integrations"
                / "notebook_ai_chatgpt_app"
                / "dist"
                / "server"
                / "index.js"
            ),
        )

    def ensure(self) -> None:
        for directory in (
            self.runtime_dir,
            self.logs_dir,
            self.config_dir,
            self.control_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TunnelConfig:
    driver: TunnelDriver = TunnelDriver.OPENAI_SECURE_TUNNEL
    tunnel_id: str | None = None
    profile: str = DEFAULT_TUNNEL_PROFILE
    client_path: str | None = None
    ready_url: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TunnelConfig":
        tunnel_id = _clean_identifier(value.get("tunnel_id"), "tunnel_id", optional=True)
        profile = _clean_identifier(
            value.get("profile") or DEFAULT_TUNNEL_PROFILE,
            "profile",
            optional=False,
        )
        client_path = value.get("client_path")
        ready_url = _local_ready_url(value.get("ready_url"))
        return cls(
            driver=TunnelDriver(
                value.get("driver", TunnelDriver.OPENAI_SECURE_TUNNEL.value)
            ),
            tunnel_id=tunnel_id,
            profile=profile or DEFAULT_TUNNEL_PROFILE,
            client_path=str(client_path).strip() if client_path else None,
            ready_url=ready_url,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "driver": self.driver.value,
            "tunnel_id": self.tunnel_id,
            "profile": self.profile,
            "client_path": self.client_path,
            "ready_url": self.ready_url,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    paths: RuntimePaths
    python_exe: Path
    node_exe: Path
    backend_port: int = DEFAULT_BACKEND_PORT
    mcp_port: int = DEFAULT_MCP_PORT
    backend_url: str = "http://127.0.0.1:8000"
    frontend_url: str = "http://127.0.0.1:5173"
    tunnel_target: str = DEFAULT_TUNNEL_TARGET
    tunnel: TunnelConfig = TunnelConfig()
    mode: str = "local"
    health_timeout_seconds: float = 60.0
    monitor_interval_seconds: float = 2.0
    max_restart_count: int = 5

    @classmethod
    def load(
        cls,
        *,
        project_root: str | Path = PROJECT_ROOT,
        env: Mapping[str, str] | None = None,
    ) -> "RuntimeConfig":
        environment = os.environ if env is None else env
        paths = RuntimePaths.resolve(project_root=project_root, env=environment)
        stored: dict[str, Any] = {}
        if paths.runtime_config_file.is_file():
            stored = json.loads(paths.runtime_config_file.read_text(encoding="utf-8"))
            if stored.get("schema_version") != RUNTIME_CONFIG_SCHEMA_VERSION:
                raise ValueError("unsupported runtime configuration schema")
            if _contains_forbidden_secret_key(stored):
                raise ValueError("runtime_config_contains_forbidden_secret")
        mode = str(environment.get("NOTEBOOK_AI_RUNTIME_MODE") or stored.get("mode") or "local")
        if mode not in {"local", "remote", "hybrid"}:
            raise ValueError("NOTEBOOK_AI runtime mode must be local, remote, or hybrid")
        python_exe = Path(
            environment.get("NOTEBOOK_AI_PYTHON_EXE")
            or stored.get("python_exe")
            or DEFAULT_PYTHON_EXE
        )
        node_value = environment.get("NOTEBOOK_AI_NODE_EXE") or stored.get("node_exe")
        node_exe = Path(node_value or shutil.which("node") or "node.exe")
        backend_port = _port(
            environment.get("NOTEBOOK_AI_BACKEND_PORT")
            or stored.get("backend_port")
            or DEFAULT_BACKEND_PORT,
            "backend_port",
        )
        mcp_port = _port(
            environment.get("NOTEBOOK_AI_MCP_PORT")
            or stored.get("mcp_port")
            or DEFAULT_MCP_PORT,
            "mcp_port",
        )
        backend_url = str(
            environment.get("NOTEBOOK_AI_BACKEND_URL")
            or stored.get("backend_url")
            or f"http://127.0.0.1:{backend_port}"
        ).rstrip("/")
        frontend_url = str(
            environment.get("NOTEBOOK_AI_FRONTEND_URL")
            or stored.get("frontend_url")
            or "http://127.0.0.1:5173"
        ).rstrip("/")
        tunnel = TunnelConfig.from_dict(stored.get("tunnel") or {})
        return cls(
            paths=paths,
            python_exe=python_exe,
            node_exe=node_exe,
            backend_port=backend_port,
            mcp_port=mcp_port,
            backend_url=backend_url,
            frontend_url=frontend_url,
            tunnel_target=f"http://127.0.0.1:{mcp_port}/mcp",
            tunnel=tunnel,
            mode=mode,
            health_timeout_seconds=float(stored.get("health_timeout_seconds") or 60.0),
            monitor_interval_seconds=float(stored.get("monitor_interval_seconds") or 2.0),
            max_restart_count=int(stored.get("max_restart_count") or 5),
        )

    def to_persisted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
            "mode": self.mode,
            "python_exe": str(self.python_exe),
            "node_exe": str(self.node_exe),
            "backend_port": self.backend_port,
            "mcp_port": self.mcp_port,
            "backend_url": self.backend_url,
            "frontend_url": self.frontend_url,
            "health_timeout_seconds": self.health_timeout_seconds,
            "monitor_interval_seconds": self.monitor_interval_seconds,
            "max_restart_count": self.max_restart_count,
            "tunnel": self.tunnel.to_dict(),
        }

    def save(self) -> None:
        self.paths.ensure()
        atomic_write_json(self.paths.runtime_config_file, self.to_persisted_dict())

    def with_tunnel(
        self,
        *,
        tunnel_id: str | None,
        profile: str,
        driver: TunnelDriver = TunnelDriver.OPENAI_SECURE_TUNNEL,
        client_path: str | None = None,
        ready_url: str | None = None,
    ) -> "RuntimeConfig":
        validated = TunnelConfig.from_dict(
            {
                "driver": driver.value,
                "tunnel_id": tunnel_id,
                "profile": profile,
                "client_path": client_path,
                "ready_url": ready_url,
            }
        )
        return RuntimeConfig(
            paths=self.paths,
            python_exe=self.python_exe,
            node_exe=self.node_exe,
            backend_port=self.backend_port,
            mcp_port=self.mcp_port,
            backend_url=self.backend_url,
            frontend_url=self.frontend_url,
            tunnel_target=self.tunnel_target,
            tunnel=validated,
            mode=self.mode,
            health_timeout_seconds=self.health_timeout_seconds,
            monitor_interval_seconds=self.monitor_interval_seconds,
            max_restart_count=self.max_restart_count,
        )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _port(value: Any, label: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError(f"{label} must be from 1 to 65535")
    return result


def _clean_identifier(value: Any, label: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    result = str(value or "").strip()
    if not result and optional:
        return None
    if not result or len(result) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in result):
        raise ValueError(f"invalid {label}")
    return result


def _contains_forbidden_secret_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in FORBIDDEN_SECRET_CONFIG_KEYS:
                return True
            if _contains_forbidden_secret_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_secret_key(child) for child in value)
    return False


def _local_ready_url(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    result = str(value).strip()
    parsed = urlparse(result)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port is None
        or parsed.path != "/readyz"
    ):
        raise ValueError(
            "tunnel ready_url must be an explicit loopback HTTP /readyz URL"
        )
    return result

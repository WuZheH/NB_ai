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

from app.core.paths import DATA_DIR, RUNTIME_PROJECT_ROOT
from app.runtime.contracts import TunnelDriver


RUNTIME_CONFIG_SCHEMA_VERSION = "notebook_ai.runtime.config.v1"
DEFAULT_PYTHON_EXE = Path(sys.executable).resolve()
DEFAULT_NODE_EXE = Path(shutil.which("node.exe") or shutil.which("node") or "node.exe")
DEFAULT_BACKEND_PORT = 8000
DEFAULT_MCP_PORT = 8787
DEFAULT_TUNNEL_PROFILE = "notebook-ai"
DEFAULT_TUNNEL_TARGET = "http://127.0.0.1:8787/mcp"
FORBIDDEN_SECRET_CONFIG_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "password", "secret", "token"}
)


@dataclass(frozen=True)
class RuntimePaths:
    runtime_root: Path
    data_project_root: Path
    data_dir: Path
    local_app_data: Path
    roaming_app_data: Path
    runtime_dir: Path
    logs_dir: Path
    config_dir: Path
    status_file: Path
    supervisor_lock: Path
    supervisor_stop_file: Path
    control_dir: Path
    runtime_config_file: Path
    legacy_runtime_config_file: Path
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
        runtime_root: str | Path | None = None,
        data_project_root: str | Path | None = None,
        data_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "RuntimePaths":
        environment = os.environ if env is None else env
        local_value = environment.get("LOCALAPPDATA")
        if not local_value:
            raise RuntimeError("LOCALAPPDATA is required for the Windows runtime launcher")
        if runtime_root is not None and project_root is not None:
            if Path(runtime_root).resolve() != Path(project_root).resolve():
                raise ValueError("runtime_root conflicts with legacy project_root")
        configured_runtime_root = runtime_root or project_root or RUNTIME_PROJECT_ROOT
        root = Path(configured_runtime_root).resolve()
        configured_data_dir = data_dir or environment.get("SEARCH_DATA_DIR")
        if configured_data_dir:
            resolved_data_dir = _resolve_path(configured_data_dir, base=root)
        else:
            configured_data_root = (
                data_project_root
                or environment.get("NOTEBOOK_AI_DATA_PROJECT_ROOT")
                or project_root
            )
            resolved_data_dir = (
                _resolve_path(configured_data_root, base=root) / "data"
                if configured_data_root
                else DATA_DIR.resolve()
            )
        data_root = resolved_data_dir.parent
        local_base = Path(local_value).expanduser().resolve()
        roaming_value = environment.get("APPDATA")
        roaming_base = (
            Path(roaming_value).expanduser().resolve()
            if roaming_value
            else local_base
        )
        local_root = local_base / "Search"
        roaming_root = roaming_base / "Search"
        legacy_local_root = local_base / "NOTEBOOK_AI"
        runtime_dir = _optional_path(
            environment.get("SEARCH_RUNTIME_DIR"),
            default=local_root / "runtime",
            base=root,
        )
        logs_dir = _optional_path(
            environment.get("SEARCH_LOG_DIR"),
            default=local_root / "logs",
            base=root,
        )
        config_dir = _optional_path(
            environment.get("SEARCH_CONFIG_DIR"),
            default=roaming_root / "config",
            base=root,
        )
        return cls(
            runtime_root=root,
            data_project_root=data_root,
            data_dir=resolved_data_dir,
            local_app_data=local_root,
            roaming_app_data=roaming_root,
            runtime_dir=runtime_dir,
            logs_dir=logs_dir,
            config_dir=config_dir,
            status_file=runtime_dir / "status.json",
            supervisor_lock=runtime_dir / "supervisor.lock",
            supervisor_stop_file=runtime_dir / "stop.request",
            control_dir=runtime_dir / "control",
            runtime_config_file=config_dir / "runtime.json",
            legacy_runtime_config_file=legacy_local_root / "config" / "runtime.json",
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

    @property
    def project_root(self) -> Path:
        """Compatibility alias for code that still names the runtime cwd root."""

        return self.runtime_root

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
        runtime_root: str | Path | None = None,
        data_project_root: str | Path | None = None,
        data_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "RuntimeConfig":
        environment = os.environ if env is None else env
        paths = RuntimePaths.resolve(
            runtime_root=runtime_root,
            data_project_root=data_project_root,
            data_dir=data_dir,
            project_root=project_root,
            env=environment,
        )
        stored: dict[str, Any] = {}
        runtime_config_file = paths.runtime_config_file
        if not runtime_config_file.is_file() and paths.legacy_runtime_config_file.is_file():
            runtime_config_file = paths.legacy_runtime_config_file
        if runtime_config_file.is_file():
            stored = json.loads(runtime_config_file.read_text(encoding="utf-8"))
            if stored.get("schema_version") != RUNTIME_CONFIG_SCHEMA_VERSION:
                raise ValueError("unsupported runtime configuration schema")
            if _contains_forbidden_secret_key(stored):
                raise ValueError("runtime_config_contains_forbidden_secret")
        mode = str(
            environment.get("SEARCH_RUNTIME_MODE")
            or environment.get("NOTEBOOK_AI_RUNTIME_MODE")
            or stored.get("mode")
            or "local"
        )
        if mode not in {"local", "remote", "hybrid"}:
            raise ValueError("Search runtime mode must be local, remote, or hybrid")
        python_exe = Path(
            environment.get("SEARCH_PYTHON")
            or environment.get("NOTEBOOK_AI_PYTHON_EXE")
            or stored.get("python_exe")
            or DEFAULT_PYTHON_EXE
        )
        node_value = (
            environment.get("SEARCH_NODE")
            or environment.get("NOTEBOOK_AI_NODE_EXE")
            or stored.get("node_exe")
        )
        node_exe = Path(node_value or DEFAULT_NODE_EXE)
        backend_port = _port(
            environment.get("SEARCH_BACKEND_PORT")
            or environment.get("NOTEBOOK_AI_BACKEND_PORT")
            or stored.get("backend_port")
            or DEFAULT_BACKEND_PORT,
            "backend_port",
        )
        mcp_port = _port(
            environment.get("SEARCH_MCP_PORT")
            or environment.get("NOTEBOOK_AI_MCP_PORT")
            or stored.get("mcp_port")
            or DEFAULT_MCP_PORT,
            "mcp_port",
        )
        backend_url = str(
            environment.get("SEARCH_BACKEND_URL")
            or environment.get("NOTEBOOK_AI_BACKEND_URL")
            or stored.get("backend_url")
            or f"http://127.0.0.1:{backend_port}"
        ).rstrip("/")
        frontend_url = str(
            environment.get("SEARCH_FRONTEND_URL")
            or environment.get("NOTEBOOK_AI_FRONTEND_URL")
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


def _resolve_path(value: str | Path, *, base: Path) -> Path:
    cleaned = str(value or "").strip()
    if not cleaned or "\x00" in cleaned:
        raise ValueError("configured path is invalid")
    candidate = Path(cleaned).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _optional_path(value: str | Path | None, *, default: Path, base: Path) -> Path:
    return _resolve_path(value, base=base) if value else default.resolve()


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

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


RUNTIME_SCHEMA_VERSION = "notebook_ai.runtime.v1"
CONTROL_SCHEMA_VERSION = "notebook_ai.runtime.control.v1"
ALLOWED_ZOTERO_CONTROL_ACTIONS = frozenset({"restart", "sync_zotero_notes"})
ALLOWED_RUNTIME_CONTROL_ACTIONS = frozenset(
    {*ALLOWED_ZOTERO_CONTROL_ACTIONS, "pause_tunnel", "resume_tunnel"}
)


class ComponentName(StrEnum):
    SUPERVISOR = "supervisor"
    FASTAPI = "fastapi"
    MCP = "mcp"
    TUNNEL = "tunnel"
    ZOTERO_NOTE_INDEX = "zotero_note_index"


class ComponentState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    EXTERNAL = "external"


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    LOCAL_READY_TUNNEL_MISSING = "local_ready_tunnel_missing"
    DEGRADED = "degraded"
    FAILED = "failed"


class TunnelDriver(StrEnum):
    OPENAI_SECURE_TUNNEL = "openai_secure_tunnel"
    CLOUDFLARE_QUICK_DEV = "cloudflare_quick_dev"
    NONE = "none"


class TunnelState(StrEnum):
    NOT_CONFIGURED = "tunnel_not_configured"
    CLIENT_MISSING = "tunnel_client_missing"
    ID_MISSING = "tunnel_id_missing"
    AUTH_MISSING = "tunnel_auth_missing"
    STARTING = "tunnel_starting"
    READY = "tunnel_ready"
    UNHEALTHY = "tunnel_unhealthy"
    QUICK_ONLINE = "quick_tunnel_online"
    PERSISTENT_CONFIGURED = "persistent_tunnel_configured"
    PERSISTENT_ONLINE = "persistent_tunnel_online"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_time: float
    executable: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProcessIdentity":
        return cls(
            pid=int(value["pid"]),
            creation_time=float(value["creation_time"]),
            executable=str(value["executable"]),
        )


@dataclass
class ComponentStatus:
    component: ComponentName
    state: ComponentState
    pid: int | None = None
    port: int | None = None
    error_code: str | None = None
    restart_count: int = 0
    owned: bool = False
    identity: ProcessIdentity | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["component"] = self.component.value
        value["state"] = self.state.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ComponentStatus":
        identity = value.get("identity")
        return cls(
            component=ComponentName(value["component"]),
            state=ComponentState(value["state"]),
            pid=int(value["pid"]) if value.get("pid") is not None else None,
            port=int(value["port"]) if value.get("port") is not None else None,
            error_code=value.get("error_code"),
            restart_count=int(value.get("restart_count") or 0),
            owned=bool(value.get("owned")),
            identity=ProcessIdentity.from_dict(identity) if identity else None,
        )


@dataclass
class RuntimeStatus:
    state: RuntimeState
    updated_at: str
    components: dict[str, ComponentStatus] = field(default_factory=dict)
    tunnel_state: TunnelState = TunnelState.NOT_CONFIGURED
    tunnel_type: str = "none"
    tunnel_url: str | None = None
    tunnel_config_path: str | None = None
    tunnel_credentials_present: bool = False
    schema_version: str = RUNTIME_SCHEMA_VERSION
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "updated_at": self.updated_at,
            "tunnel_state": self.tunnel_state.value,
            "tunnel_type": self.tunnel_type,
            "tunnel_url": self.tunnel_url,
            "tunnel_config_path": self.tunnel_config_path,
            "tunnel_credentials_present": self.tunnel_credentials_present,
            "error_code": self.error_code,
            "components": {
                name: component.to_dict()
                for name, component in sorted(self.components.items())
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeStatus":
        if value.get("schema_version") != RUNTIME_SCHEMA_VERSION:
            raise ValueError("unsupported runtime status schema")
        return cls(
            state=RuntimeState(value["state"]),
            updated_at=str(value["updated_at"]),
            tunnel_state=TunnelState(
                value.get("tunnel_state", TunnelState.NOT_CONFIGURED.value)
            ),
            tunnel_type=str(value.get("tunnel_type") or "none"),
            tunnel_url=(str(value["tunnel_url"]) if value.get("tunnel_url") else None),
            tunnel_config_path=(
                str(value["tunnel_config_path"])
                if value.get("tunnel_config_path")
                else None
            ),
            tunnel_credentials_present=bool(value.get("tunnel_credentials_present")),
            error_code=value.get("error_code"),
            components={
                str(name): ComponentStatus.from_dict(component)
                for name, component in (value.get("components") or {}).items()
            },
        )


ControlAction = Literal[
    "restart",
    "sync_zotero_notes",
    "pause_tunnel",
    "resume_tunnel",
]


@dataclass(frozen=True)
class ControlRequest:
    action: ControlAction
    request_id: str
    timestamp: str
    schema_version: str = CONTROL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_RUNTIME_CONTROL_ACTIONS:
            raise ValueError("unsupported runtime control action")
        if (
            not self.request_id
            or len(self.request_id) > 128
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for character in self.request_id
            )
        ):
            raise ValueError("invalid runtime control request id")
        if not self.timestamp or len(self.timestamp) > 64:
            raise ValueError("invalid runtime control timestamp")
        try:
            parsed_timestamp = datetime.fromisoformat(self.timestamp)
        except ValueError as exc:
            raise ValueError("invalid runtime control timestamp") from exc
        if parsed_timestamp.tzinfo is None:
            raise ValueError("invalid runtime control timestamp")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ControlRequest":
        if value.get("schema_version") != CONTROL_SCHEMA_VERSION:
            raise ValueError("unsupported runtime control request schema")
        allowed_keys = {"schema_version", "action", "request_id", "timestamp"}
        if set(value).difference(allowed_keys):
            raise ValueError("runtime control request contains forbidden fields")
        return cls(
            action=str(value["action"]),  # type: ignore[arg-type]
            request_id=str(value["request_id"]),
            timestamp=str(value["timestamp"]),
        )

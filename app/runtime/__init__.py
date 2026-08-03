"""Local NOTEBOOK_AI runtime supervision.

The runtime package owns local process lifecycle only.  Search, persistence,
MCP protocol handling, and Zotero data extraction stay in their existing
domains.
"""

__all__ = [
    "ComponentName",
    "ComponentState",
    "RuntimeConfig",
    "RuntimePaths",
    "RuntimeState",
    "TunnelState",
]


def __getattr__(name: str):
    if name in {"RuntimeConfig", "RuntimePaths"}:
        from app.runtime.config import RuntimeConfig, RuntimePaths

        return {"RuntimeConfig": RuntimeConfig, "RuntimePaths": RuntimePaths}[name]
    if name in {"ComponentName", "ComponentState", "RuntimeState", "TunnelState"}:
        from app.runtime.contracts import (
            ComponentName,
            ComponentState,
            RuntimeState,
            TunnelState,
        )

        return {
            "ComponentName": ComponentName,
            "ComponentState": ComponentState,
            "RuntimeState": RuntimeState,
            "TunnelState": TunnelState,
        }[name]
    raise AttributeError(name)

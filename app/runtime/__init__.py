"""Local NOTEBOOK_AI runtime supervision.

The runtime package owns local process lifecycle only.  Search, persistence,
MCP protocol handling, and Zotero data extraction stay in their existing
domains.
"""

from app.runtime.config import RuntimeConfig, RuntimePaths
from app.runtime.contracts import (
    ComponentName,
    ComponentState,
    RuntimeState,
    TunnelState,
)

__all__ = [
    "ComponentName",
    "ComponentState",
    "RuntimeConfig",
    "RuntimePaths",
    "RuntimeState",
    "TunnelState",
]

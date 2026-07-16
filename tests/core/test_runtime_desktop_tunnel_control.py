from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.cli import build_parser
from app.runtime.config import RuntimeConfig
from app.runtime.contracts import ComponentName, ComponentState, ComponentStatus, RuntimeState, TunnelState
from app.runtime.supervisor import RuntimeSupervisor
from app.runtime.tunnel import ChatGptTunnelStatus


def _config(tmp_path: Path) -> RuntimeConfig:
    project = tmp_path / "project"
    project.mkdir()
    return RuntimeConfig.load(
        project_root=project,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
        },
    )


def _ready_local_components(supervisor: RuntimeSupervisor) -> None:
    for name in (ComponentName.FASTAPI, ComponentName.MCP):
        supervisor.status.components[name.value] = ComponentStatus(
            component=name,
            state=ComponentState.READY,
            owned=True,
        )


def test_legacy_tunnel_signals_remain_compatible() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if action.dest == "command"  # noqa: SLF001
    )
    signal_parser = subparsers.choices["signal"]
    action = next(
        item for item in signal_parser._actions if item.dest == "action"  # noqa: SLF001
    )
    assert {"pause_tunnel", "resume_tunnel"}.issubset(action.choices)


@pytest.mark.parametrize("method_name", ["_pause_tunnel", "_resume_tunnel"])
def test_legacy_tunnel_signal_is_detection_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    _ready_local_components(supervisor)
    fastapi = supervisor.status.components[ComponentName.FASTAPI.value]
    mcp = supervisor.status.components[ComponentName.MCP.value]
    monkeypatch.setattr(
        supervisor.tunnel_probe,
        "diagnose",
        lambda: ChatGptTunnelStatus(
            "quick",
            TunnelState.QUICK_ONLINE,
            pid=40036,
            public_url="https://temporary.trycloudflare.com",
        ),
    )
    monkeypatch.setattr(
        supervisor.process_manager,
        "stop",
        lambda value: pytest.fail("detection-only signal stopped a process"),
    )
    monkeypatch.setattr(
        supervisor,
        "_start_tunnel",
        lambda: pytest.fail("detection-only signal started a tunnel"),
    )
    monkeypatch.setattr(
        supervisor,
        "_persist",
        lambda state, **kwargs: setattr(supervisor.status, "state", state),
    )

    getattr(supervisor, method_name)()

    assert supervisor.status.components[ComponentName.FASTAPI.value] is fastapi
    assert supervisor.status.components[ComponentName.MCP.value] is mcp
    assert supervisor.status.components[ComponentName.TUNNEL.value].owned is False
    assert supervisor.status.components[ComponentName.TUNNEL.value].pid == 40036
    assert supervisor.status.tunnel_state is TunnelState.QUICK_ONLINE
    assert supervisor.status.state is RuntimeState.LOCAL_READY_TUNNEL_MISSING

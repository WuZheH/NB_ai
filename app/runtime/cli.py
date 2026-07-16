from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser
from typing import Any, Sequence

from app.runtime.config import RuntimeConfig
from app.runtime.contracts import TunnelDriver
from app.runtime.supervisor import RuntimeController, RuntimeStartupError, RuntimeSupervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notebook-ai-runtime",
        description="Manage the local NOTEBOOK_AI FastAPI, MCP, and secure-tunnel runtime.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "ensure-running", "stop", "restart", "status", "doctor"):
        commands.add_parser(name)
    logs = commands.add_parser("logs")
    logs.add_argument("--open", action="store_true", dest="open_directory")
    commands.add_parser("open-web")
    commands.add_parser("supervise", help=argparse.SUPPRESS)

    configure = commands.add_parser("configure-tunnel")
    configure.add_argument("--tunnel-id")
    configure.add_argument("--profile")
    configure.add_argument(
        "--driver",
        choices=[driver.value for driver in TunnelDriver],
        default=TunnelDriver.OPENAI_SECURE_TUNNEL.value,
    )
    configure.add_argument("--client-path")
    configure.add_argument("--ready-url")
    commands.add_parser("tunnel-status")
    commands.add_parser("tunnel-doctor")
    signal = commands.add_parser("signal")
    signal.add_argument(
        "action",
        choices=[
            "restart",
            "sync_zotero_notes",
            "pause_tunnel",
            "resume_tunnel",
        ],
    )
    signal.add_argument("--request-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        config = RuntimeConfig.load()
        controller = RuntimeController(config)
        if arguments.command in {"start", "ensure-running"}:
            return _emit(controller.start().to_dict())
        if arguments.command == "stop":
            return _emit(controller.stop().to_dict())
        if arguments.command == "restart":
            return _emit(controller.restart().to_dict())
        if arguments.command == "status":
            return _emit(controller.status().to_dict())
        if arguments.command == "doctor":
            return _emit(controller.doctor())
        if arguments.command == "logs":
            if arguments.open_directory:
                config.paths.logs_dir.mkdir(parents=True, exist_ok=True)
                _open_directory(config.paths.logs_dir)
            return _emit({"logs_dir": str(config.paths.logs_dir)})
        if arguments.command == "open-web":
            webbrowser.open(config.frontend_url, new=0, autoraise=True)
            return _emit({"status": "open_requested", "target": "notebook_ai_local_web"})
        if arguments.command == "supervise":
            return RuntimeSupervisor(config).supervise_forever()
        if arguments.command == "configure-tunnel":
            driver = TunnelDriver(arguments.driver)
            if driver is TunnelDriver.OPENAI_SECURE_TUNNEL and not arguments.tunnel_id:
                raise ValueError("--tunnel-id is required for openai_secure_tunnel")
            configured = config.with_tunnel(
                tunnel_id=arguments.tunnel_id,
                profile=arguments.profile or config.tunnel.profile,
                driver=driver,
                client_path=arguments.client_path or config.tunnel.client_path,
                ready_url=arguments.ready_url or config.tunnel.ready_url,
            )
            configured.save()
            return _emit(
                {
                    "status": "configured",
                    "driver": configured.tunnel.driver.value,
                    "tunnel_id_configured": bool(configured.tunnel.tunnel_id),
                    "profile": configured.tunnel.profile,
                    "secret_saved": False,
                    "client_initialization": (
                        "documented_init_required"
                        if driver is TunnelDriver.OPENAI_SECURE_TUNNEL
                        else "not_applicable"
                    ),
                }
            )
        if arguments.command == "tunnel-status":
            return _emit(controller.tunnel_status(run_doctor=False))
        if arguments.command == "tunnel-doctor":
            return _emit(controller.tunnel_status(run_doctor=True))
        if arguments.command == "signal":
            controller.signal(arguments.action, request_id=arguments.request_id)
            return _emit({"status": "accepted", "action": arguments.action})
    except RuntimeStartupError as exc:
        return _emit_error(exc.error_code)
    except (OSError, RuntimeError, ValueError) as exc:
        error_code = _safe_error_code(exc)
        return _emit_error(error_code)
    return _emit_error("unknown_runtime_command")


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


def _emit_error(error_code: str) -> int:
    print(json.dumps({"status": "error", "error_code": error_code}, sort_keys=True))
    return 1


def _safe_error_code(exc: BaseException) -> str:
    message = str(exc).strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    if message and len(message) <= 96 and all(character in allowed for character in message):
        return message
    return type(exc).__name__.lower()


def _open_directory(path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("logs_open_windows_only")
    os.startfile(str(path))  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.runtime.config import RuntimeConfig


AUTOSTART_TASK_NAME = "NOTEBOOK_AI Runtime Launcher"
AUTOSTART_DELAY_SECONDS = 20


@dataclass(frozen=True)
class AutostartContract:
    task_name: str
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    delay_seconds: int
    current_user_only: bool = True
    start_when_available: bool = True
    multiple_instances: str = "ignore_new"


def build_autostart_contract(config: RuntimeConfig) -> AutostartContract:
    return AutostartContract(
        task_name=AUTOSTART_TASK_NAME,
        executable=config.python_exe,
        arguments=("-B", str(config.paths.launcher_script), "start"),
        working_directory=config.paths.project_root,
        delay_seconds=AUTOSTART_DELAY_SECONDS,
    )

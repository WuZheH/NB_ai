from __future__ import annotations

from pathlib import Path

from app.runtime.autostart import (
    AUTOSTART_DELAY_SECONDS,
    AUTOSTART_TASK_NAME,
    build_autostart_contract,
)
from app.runtime.config import RuntimeConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig.load(
        project_root=PROJECT_ROOT,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "NOTEBOOK_AI_PYTHON_EXE": r"D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe",
            "NOTEBOOK_AI_NODE_EXE": "node.exe",
        },
    )


def test_autostart_contract_is_current_user_delayed_and_idempotent(
    tmp_path: Path,
) -> None:
    contract = build_autostart_contract(_config(tmp_path))
    assert contract.task_name == AUTOSTART_TASK_NAME
    assert AUTOSTART_DELAY_SECONDS == 20
    assert contract.current_user_only is True
    assert contract.start_when_available is True
    assert contract.multiple_instances == "ignore_new"
    assert contract.arguments[-1] == "start"
    assert contract.working_directory == PROJECT_ROOT.resolve()


def test_task_scheduler_scripts_use_only_the_owned_current_user_task() -> None:
    runtime = PROJECT_ROOT / "integrations" / "search_desktop" / "scripts"
    install = (runtime / "install-autostart.ps1").read_text(encoding="utf-8")
    uninstall = (runtime / "uninstall-autostart.ps1").read_text(encoding="utf-8")
    status = (runtime / "status-autostart.ps1").read_text(encoding="utf-8")
    common = (runtime / "autostart-common.ps1").read_text(encoding="utf-8")
    assert "SearchDesktopTaskName" in install
    assert "New-ScheduledTaskTrigger -AtLogOn -User $Identity.name" in install
    assert '$Trigger.Delay = "PT20S"' in install
    assert "-StartWhenAvailable" in install
    assert "-MultipleInstances IgnoreNew" in install
    assert "-RunLevel Limited" in install
    assert "Register-ScheduledTask" in install and "-Force" in install
    assert "Unregister-ScheduledTask -TaskName $SearchDesktopTaskName" in uninstall
    assert "Test-OwnedSearchDesktopTask" in install
    assert "Test-OwnedSearchDesktopTask" in uninstall
    assert "Test-OwnedSearchDesktopTask" in status
    assert "search_desktop_autostart_ownership_mismatch" in install
    assert "search_desktop_autostart_ownership_mismatch" in uninstall
    assert 'status = "ownership_mismatch"' in status
    assert "$Task.Description -cne $SearchDesktopTaskDescription" in common
    assert "$Action.Execute $ExecutablePath" in common
    assert "$Task.Principal.UserId" in common
    assert "-TaskPath $SearchDesktopTaskPath" in install
    assert "-TaskPath $SearchDesktopTaskPath" in uninstall
    assert "Get-ScheduledTask -TaskName $SearchDesktopTaskName" in status
    forbidden = ("HKCU", "CurrentVersion\\Run", "Startup", "New-Service", "sc.exe")
    assert not any(value in install for value in forbidden)


def test_script_only_launcher_does_not_modify_app_cli_contract() -> None:
    launcher = PROJECT_ROOT / "scripts" / "runtime" / "notebook_ai_launcher.py"
    assert launcher.is_file()
    assert "from app.runtime.cli import main" in launcher.read_text(encoding="utf-8")
    assert "register_" not in launcher.read_text(encoding="utf-8")

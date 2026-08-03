from __future__ import annotations

import hashlib
import json

import typer

from app import cli, cli_runtime
from app.cli_commands import app as grouped_app
from app.cli_commands.zotero import ZOTERO_COMMANDS


EXPECTED_COMMAND_TREE_COUNT = 53
EXPECTED_COMMAND_TREE_FINGERPRINT = (
    "fbe43bc7aedd1ff692188b3474e908159fe69c0a86f86f6798b61576a5395a7e"
)


def _command_tree_contract() -> list[dict[str, object]]:
    root = typer.main.get_command(cli.app)
    rows: list[dict[str, object]] = []

    def walk(command, prefix: str = "") -> None:
        for name, child in (getattr(command, "commands", None) or {}).items():
            path = f"{prefix} {name}".strip()
            parameters = []
            for parameter in child.params:
                parameters.append(
                    {
                        "name": parameter.name,
                        "type": type(parameter).__name__,
                        "required": bool(getattr(parameter, "required", False)),
                        "multiple": bool(getattr(parameter, "multiple", False)),
                        "nargs": getattr(parameter, "nargs", None),
                        "default": repr(getattr(parameter, "default", None)),
                        "opts": list(getattr(parameter, "opts", []) or []),
                        "secondary_opts": list(
                            getattr(parameter, "secondary_opts", []) or []
                        ),
                    }
                )
            rows.append({"path": path, "params": parameters})
            walk(child, path)

    walk(root)
    return rows


def test_cli_facade_keeps_the_same_typer_application() -> None:
    assert cli.app is cli_runtime.app
    assert grouped_app is cli.app
    assert cli.inspiration_card_app is cli_runtime.inspiration_card_app
    assert ZOTERO_COMMANDS == ()


def test_cli_command_names_and_parameters_match_the_legacy_contract() -> None:
    contract = _command_tree_contract()
    encoded = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(contract) == EXPECTED_COMMAND_TREE_COUNT
    assert hashlib.sha256(encoded).hexdigest() == EXPECTED_COMMAND_TREE_FINGERPRINT

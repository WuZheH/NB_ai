from __future__ import annotations

import importlib as _importlib
import runpy as _runpy
import sys as _sys
from pathlib import Path as _Path


_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

_IMPLEMENTATION_MODULE = "scripts.zotero.phase110_r3a_sync_zotero_notes_for_unit"

if __name__ == "__main__":
    _runpy.run_module(_IMPLEMENTATION_MODULE, run_name="__main__")
else:
    _implementation = _importlib.import_module(_IMPLEMENTATION_MODULE)
    for _value in vars(_implementation).values():
        if getattr(_value, "__module__", None) == _implementation.__name__:
            try:
                _value.__module__ = __name__
            except (AttributeError, TypeError):
                pass
    globals().update(
        {
            _name: _value
            for _name, _value in vars(_implementation).items()
            if not _name.startswith("__")
        }
    )
    __all__ = [name for name in globals() if not name.startswith("_")]


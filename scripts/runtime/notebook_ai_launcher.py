from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = str(PROJECT_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != RUNTIME_ROOT]
sys.path.insert(0, RUNTIME_ROOT)

from app.runtime.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

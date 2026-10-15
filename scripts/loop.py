#!/usr/bin/env python3
"""Works from a source checkout or an installed .loop directory."""
from pathlib import Path
import sys

parent = Path(__file__).resolve().parent
sys.path.insert(0, str(parent if (parent / "loop_work").is_dir() else parent.parent))

from loop_work.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

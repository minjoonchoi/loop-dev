#!/usr/bin/env python3
"""Enter the source checkout or the self-contained copied project installation."""
from pathlib import Path
import runpy


skill = Path(__file__).resolve().parent
base = skill.parent.parent
# Provider discovery folders may already contain unrelated scripts. Their helper
# belongs to the copied project's runtime, never to a guessed neighbouring script.
if base.name in (".agents", ".claude"):
    entrypoint = skill.parents[2] / ".loop/loop.py"
else:
    entrypoint = base / "scripts/loop.py"
if not entrypoint.is_file():
    raise SystemExit("loop-dev is unavailable; stop harness routing and reinstall before explicit activation.")
runpy.run_path(str(entrypoint), run_name="__main__")
